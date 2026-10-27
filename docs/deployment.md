# Deployment guide

Everything is deployed in the AWS console in **us-east-1**. Resource names below are the ones the code and policies expect.

## 1. Foundations

1. **Bedrock model access.** Enable Anthropic Claude (Sonnet for the agent, Haiku for evaluation judges) and Amazon Titan Text Embeddings V2. Copy the Claude cross-region inference profile ID for `MODEL_ID`.
2. **S3 buckets:** `airxpact-knowledge-<account>`, `airxpact-build-<account>`, `airxpact-trip-files-<account>` (30-day expiry rule).
3. **Knowledge Base** `airxpact-aviation-library`: S3 data source `airxpact-docs` pointing at the knowledge bucket (upload `knowledge/*.md`), Titan Text Embeddings V2, S3 Vectors store. Sync it.
4. **Secrets Manager:**
   - `airxpact/duffel`: `{"api_key": "..."}`
   - `airxpact/providers`: `{"google_maps_api_key": "...", "aviationstack_api_key": "..."}`
   - `airxpact/whatsapp`: `{"cognito_client_id": "...", "cognito_client_secret": "..."}` (Twilio keys are added only if the Twilio channel is used)

## 2. Tools

1. **DynamoDB:** `airxpact-bookings` (partition key `booking_id`, String, TTL `expires_at`) and `airxpact-whatsapp-sessions` (partition key `actor_id`, String, TTL `expires_at`).
2. **SNS topic** `airxpact-booking-requests` with an email subscription for the operations team.
3. **Lambda** `airxpact-aviation-tools` (Python 3.12, arm64, 256 MB, 60 s) from `functions/aviation_tools`. Set the environment variables from the README. Grant `secretsmanager:GetSecretValue` on both secrets, `s3:PutObject`/`GetObject` on `trips/*` in the trip bucket, `dynamodb:PutItem`/`GetItem` on the bookings table and `sns:Publish` on the bookings topic.

## 3. Identity and access

1. **Cognito user pool** `airxpact-users`:
   - Web app client `airxpact-web`: single-page application, authorization code grant, scopes `openid email profile`, callback and sign-out URL set to the Amplify domain.
   - Resource server `airxpact-api` with scope `chat`.
   - Machine-to-machine clients for channels and partners (`channel-whatsapp`, `partner-<name>`), each with **Client credentials** and the `airxpact-api/chat` scope.
2. **AgentCore Identity**, outbound:
   - API key `airxpact-google-places`
   - API key `airxpact-tavily`
   - OAuth client `airxpact-gateway-m2m` (custom provider, discovery URL and client credentials of the Gateway's Cognito pool, created in step 4)

## 4. Gateway and Policy

1. **Gateway** `airxpact-gateway`: inbound auth "Quick create with Cognito", IAM service role. Targets:
   - `AviationTools`: Lambda ARN target, inline schema `gateway/aviation-tools.json`, outbound IAM role.
   - `PlacesSearch`: OpenAPI target `gateway/places-openapi.json`. Outbound API key `airxpact-google-places` in header `X-Goog-Api-Key`.
   - `WebSearch`: OpenAPI target `gateway/web-search-openapi.json`. Outbound API key `airxpact-tavily` in header `Authorization` with prefix `Bearer `.
2. Allow the Gateway role `lambda:InvokeFunction` on the tools Lambda.
3. **Policy engine** `airxpact_policy_engine`: create the five policies in `policies/airxpact.cedar` (replace `GATEWAY_ARN`). Attach the engine to the Gateway in Log only mode, test, then switch to Enforce.

## 5. Memory

**AgentCore Memory** `airxpact_memory`: 90-day event expiry, with these strategies:

| Strategy | Namespace |
| --- | --- |
| User preferences | `/users/{actorId}/preferences` |
| Semantic | `/users/{actorId}/facts` |
| Summarization | `/users/{actorId}/summaries/{sessionId}` |

## 6. Agent runtime

1. Turn on CloudWatch **Transaction Search**.
2. **ECR** repository `airxpact-agent`. Zip the `agent/` folder, upload it to the build bucket as `airxpact-agent-src.zip`, and create the **CodeBuild** project `airxpact-agent-build`:
   - S3 source
   - Amazon Linux aarch64 standard image, privileged mode on
   - environment variable `ECR_REPO_URI`
   - service role with `AmazonEC2ContainerRegistryPowerUser`
   Run the build.
3. **AgentCore Runtime** `airxpact_agent` from the ECR image, HTTP protocol, public network, with the agent environment variables from the README. Add to the runtime role:
   - `bedrock:InvokeModel*` on foundation models and inference profiles
   - `bedrock:Retrieve` on the Knowledge Base
   - AgentCore Memory event and record actions on the memory
   - `GetWorkloadAccessToken*` and `GetResourceOauth2Token`
   - `secretsmanager:GetSecretValue` on `bedrock-agentcore-identity!*`
4. Set inbound auth to **JWT**: discovery URL of `airxpact-users`, allowed clients = the web client and every M2M client.

## 7. Observability and evaluations

1. Enable tracing and log delivery on the runtime, memory and gateway. Use the CloudWatch **GenAI Observability** dashboard.
2. Create custom evaluators `airxpact_aviation_quality` and `airxpact_consistency` from `evaluation/`.
3. Create the online evaluation `airxpact_online_eval` on the `DEFAULT` endpoint with the built-in evaluators listed in `evaluation/README.md`.

## 8. Web app and partner API

1. **Lambda** `airxpact-api` from `functions/api` (300 s timeout) with a Function URL (auth NONE; the runtime validates the JWT). Set CORS to the Amplify domain.
2. **Amplify Hosting:** deploy `web/` with `config.js` filled in: Cognito domain, web client ID, site URL, API URL.
3. Partners get a token from `<cognito-domain>/oauth2/token` (client credentials, scope `airxpact-api/chat`), then `POST` to the API URL with `{"prompt", "session_id", "user_id"}`.

## 9. WhatsApp through AWS End User Messaging Social

1. **SNS topic** `airxpact-whatsapp-events` (standard). Its access policy allows `social-messaging.amazonaws.com` to `sns:Publish`, with condition `aws:SourceAccount`.
2. **End User Messaging Social** > **Business accounts** > **Link business account**. Complete Meta embedded sign-up, register and verify the phone number, turn on **Event publishing** to the SNS topic, and set the phone PIN.
3. **Lambda** `airxpact-eum-whatsapp` from `functions/whatsapp_eum`:
   - Python 3.12, arm64, 256 MB, 300 s
   - async retries 0
   - environment variables from the README
   - permissions: `social-messaging:SendWhatsAppMessage` on `arn:aws:social-messaging:us-east-1:<account>:phone-number-id/*`, `secretsmanager:GetSecretValue` on `airxpact/whatsapp`, `dynamodb:GetItem`/`PutItem` on the sessions table
4. Add the SNS topic as the Lambda trigger.
5. Test with `functions/whatsapp_eum/test-event.json`: set the phone number ID, your number and a new message ID. Then message the business number from a phone.

## 10. Live knowledge

1. Add Web Crawler data sources to the Knowledge Base from `knowledge/crawl-seed-urls.txt`, using the include patterns in that file. Sync each one.
2. **Lambda** `airxpact-kb-sync` from `functions/kb_sync` with `KB_ID`. Permissions: `bedrock:ListDataSources` and `bedrock:StartIngestionJob` on the Knowledge Base.
3. **EventBridge Scheduler** `airxpact-kb-daily-refresh`: cron `0 4 * * ? *`, time zone Africa/Lagos, target the sync Lambda.

## Optional: Twilio WhatsApp channel

**Lambda** `airxpact-whatsapp` from `functions/whatsapp_twilio`, with:

- a Function URL (auth NONE)
- environment variables from the README
- the Twilio account SID and auth token added to `airxpact/whatsapp`
- `lambda:InvokeFunction` on itself and access to the sessions table

Point the Twilio "When a message comes in" webhook at the Function URL (POST). Opening the URL in a browser shows a configuration self-check. Add `?test=agent` to send a test message to the agent.
