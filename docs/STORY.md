## Inspiration

Planning a trip by air means juggling a dozen tabs: fare search, airport weather, visa rules, airline baggage policies, the bus from the airport, currency, a translator at the other end. Most travellers I know in Nigeria do all of this on their phones, and the one app every one of them already has open is WhatsApp.

So the question behind Airxpact was simple: what if a traveller could send one message, *"I'm going to London on 12 December, plan my trip"*, to a WhatsApp number and get back what a good travel agent and an aviation expert would tell them together? No new app, no sign-up form, no website to learn.

## What it does

Airxpact is an AI travel and aviation assistant on WhatsApp. In one conversation it can:

- compare live flight offers by price, stops, layovers and baggage, and flag risky connections
- read live METAR/TAF reports and explain them in plain language, plus destination forecasts
- build a departure-day timeline and a calendar file with reminders
- find buses, trains and transfers, attractions, hotels and translators
- give a country's currency and languages, live exchange rates, and reply in the traveller's own language
- check a flight's status by flight number
- answer aviation questions from a knowledge base that refreshes itself daily from airline, authority and travel-advice websites
- take booking requests (bus, flight, transfer, translator, hotel, tour) with an `AXP-` reference that the operations team confirms

It remembers the traveller between conversations: home airport, seat preference, the trip they planned last week.

## How I built it

The whole system runs on AWS in `us-east-1` and was built entirely in the AWS console.

**Channel.** The WhatsApp Business Account and phone number are linked through **AWS End User Messaging Social**. Inbound messages are published to an Amazon SNS topic, which triggers the `whatsapp_eum` Lambda. That function drops duplicate deliveries, sends a read receipt, picks the conversation session from DynamoDB, gets a Cognito machine-to-machine token and calls the agent. The reply goes back with `SendWhatsAppMessage`.

**Agent.** The agent is written with Strands Agents, packaged as an ARM64 container by AWS CodeBuild, pushed to Amazon ECR and hosted on **Amazon Bedrock AgentCore Runtime** with JWT inbound auth. Claude on Amazon Bedrock is the model. Around it:

- **AgentCore Memory** keeps short-term conversation events and three long-term strategies: user preferences, semantic facts and per-session summaries.
- **AgentCore Gateway** turns one Lambda (11 aviation tools) and two OpenAPI targets (Google Places, Tavily web search) into MCP tools.
- **AgentCore Identity** holds the API keys and gives the agent an OAuth token for the Gateway, so no key is ever in the code.
- **AgentCore Policy** checks every tool call against Cedar policies, default deny, in Enforce mode.
- **AgentCore Observability** and **Evaluations** trace every turn and score live traffic with built-in and custom LLM-as-a-judge evaluators.

**Knowledge.** An Amazon Bedrock Knowledge Base stores an aviation library in Amazon S3 Vectors, from S3 documents and Web Crawler data sources. An EventBridge Scheduler job runs a small Lambda every morning to re-sync them.

**Privacy.** A phone number is never used as an identity. The memory actor ID is a hash:

$$
\text{actor\_id} = \texttt{"wa-"} \,\Vert\, \operatorname{SHA256}(\texttt{"whatsapp:"} \,\Vert\, \text{phone})_{[0:32]}
$$

**Sessions.** WhatsApp has no "new chat" button, so the Lambda decides when a conversation ends. With $t_{\text{last}}$ the time of the traveller's previous message, a new session starts when

$$
t_{\text{now}} - t_{\text{last}} > 6\ \text{h} \quad \lor \quad \text{message} \in \{\text{"new trip"}, \text{"reset"}, \text{"start over"}\}
$$

Long-term memory carries across sessions; only the short-term thread resets.

**Message size.** Long answers are split on paragraph boundaries into WhatsApp-sized parts of at most 3,800 characters, so an answer of length $L$ is sent as at least

$$
n = \left\lceil \frac{L}{3800} \right\rceil
$$

messages (more when paragraphs don't pack evenly), numbered so they read in order.

## Challenges I ran into

**Getting a reply back before the channel gave up.** My first WhatsApp prototype used a Twilio webhook, which must answer within 15 seconds. A full trip plan (memory lookup, several tool calls, a long answer) regularly took longer:

$$
t_{\text{token}} + t_{\text{memory}} + \sum_{i} t_{\text{tool}_i} + t_{\text{model}} > 15\ \text{s}
$$

I added a fast path that answered inside the window and handed slow turns to an asynchronous invocation. Twilio then rejected outbound messages until the business passed Trust Hub verification. Moving to AWS End User Messaging Social fixed this at the root: SNS invokes the Lambda asynchronously, so it can take up to five minutes and there is no webhook deadline to race.

**Identity between the pieces.** The first agent calls failed with *"Workload access token has not been set"*: when the runtime is invoked with IAM rather than a JWT, AgentCore does not create a workload token, so the agent could not fetch the Gateway's OAuth token. Switching the runtime to JWT inbound auth and having each channel sign in as its own Cognito client fixed it. That exposed the next error, `invalid_grant`, because the Cognito app client did not have the client-credentials grant switched on.

**IAM and API details that fail quietly.** `SendWhatsAppMessage` returned `AccessDeniedException` until the policy covered `phone-number-id/*` rather than the WABA ARN. It then returned `InvalidParametersException` because the number in the SNS event has no leading `+`; the API needs strict E.164. SNS also delivers the same event more than once, so the Lambda now writes a `msg#<id>` marker to DynamoDB with a conditional put and skips anything it has seen.

**Moving dependencies.** Partway through, the `mcp` Python package moved to 2.x and removed the client the agent used, and the Claude model I started with was retired. Both broke the container at start-up, and only the runtime logs showed why. I pinned versions in `requirements.txt` and moved the model ID into an environment variable.

**Making the agent trustworthy, not just fluent.** Early testing showed real problems:

- the agent replied "let me search for that" and then stopped
- it did arithmetic on dates and fares in its head and got it wrong
- it gave prices without saying where they came from
- it greeted returning travellers with a long introduction every time

I fixed these in the system prompt and with two local tools: a safe calculator, which parses the expression with `ast` and never runs `eval`, and a date calculator. Every price must now carry a source or be labelled as an estimate. An acceptance test suite and a custom "consistency" evaluator now check for these regressions on live traffic.

**Writing policies for tools.** Cedar actions on the Gateway are named `<Target>___<tool>`, with three underscores. Getting one character wrong silently denies the tool. I ran the policy engine in Log only mode first, read the decisions in the logs, then switched to Enforce.

## Accomplishments that I'm proud of

- One WhatsApp message produces a complete, sourced trip plan, with live data from nine providers.
- No secret lives in code. Every key sits in Secrets Manager or the AgentCore Identity vault, and every tool call is authorized by policy outside the model.
- The knowledge base keeps itself current without anyone touching it.
- Everything, including evaluations, is observable from one CloudWatch dashboard.

## What I learned

- **Pick the channel for its delivery model, not just its reach.** An event-driven channel (End User Messaging Social to SNS to Lambda) suits an agent whose turns take seconds to minutes far better than a synchronous webhook.
- **Separate identity for each caller pays off.** Giving the WhatsApp channel its own Cognito client made tokens, logs and policies easy to reason about. It is also what made the Gateway's outbound OAuth work.
- **Agents need tools for the things models are bad at.** A ten-line calculator removed a whole class of wrong answers.
- **Guardrails belong outside the prompt.** Cedar policies enforced at the Gateway hold even when the model is talked into something.
- **Observability and evaluations are development tools,** not only production tools. Most of my bugs were found by reading a trace, not by guessing.
- **Read the error before changing the code.** Nearly every blocker, from `AccessDenied` to `invalid_grant` to a missing `+`, was spelled out in a CloudWatch log line.

## What's next for Airxpact

- Paid bookings through a licensed partner, with payment links instead of manual confirmation
- Voice notes in and out for travellers who prefer speaking
- Proactive alerts: gate changes, delays and weather at the departure airport sent before the traveller asks
- More languages tested end to end, starting with Yoruba, Hausa, Igbo and French
