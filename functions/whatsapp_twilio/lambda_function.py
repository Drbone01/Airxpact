"""WhatsApp channel through Twilio webhooks."""
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

import boto3

REQUIRED_ENV = ["AGENT_RUNTIME_ARN", "SECRET_ARN", "COGNITO_DOMAIN", "COGNITO_SCOPE",
                "WHATSAPP_FROM", "SESSIONS_TABLE", "WEBHOOK_URL"]
SECRET_KEYS = ["twilio_account_sid", "twilio_auth_token", "cognito_client_id", "cognito_client_secret"]


def runtime_url():
    arn = os.environ["AGENT_RUNTIME_ARN"].strip()
    region = arn.split(":")[3]
    qualifier = os.environ.get("QUALIFIER", "DEFAULT").strip() or "DEFAULT"
    return (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(arn, safe='')}/invocations?qualifier={qualifier}")


WA_LIMIT = 1500

CHANNEL_NOTE = (
    "[Channel: WhatsApp. Reply in plain WhatsApp style: short lines, *bold* with single asterisks, "
    "no tables, no headings, no markdown links (write the URL itself). Keep it under 1,200 characters "
    "unless the traveller asked for a full itinerary. Reply in the language the traveller wrote in. "
    "Only help with travel and trip planning; politely decline unrelated requests (WhatsApp Business "
    "policy). Greetings: give the full Airxpact introduction ONLY if this conversation has no earlier "
    "messages and you have no memory of this traveller; otherwise answer a greeting with one short line "
    "such as 'Hi again! How can I help today?'. Tell new travellers once that they can send 'new trip' "
    "to start a fresh conversation. Never end with 'let me check' - finish the tool calls first.]\n\n"
)

_lambda = boto3.client("lambda")
_secrets = boto3.client("secretsmanager")
_dynamodb = boto3.resource("dynamodb")
IDLE_SECONDS = 6 * 3600
_cfg = None
_token = {"value": None, "exp": 0}
_last_agent_info = {}
_pool = ThreadPoolExecutor(max_workers=2)


def sessions_table():
    return _dynamodb.Table(os.environ["SESSIONS_TABLE"].strip())


def cfg():
    global _cfg
    if _cfg is None:
        _cfg = json.loads(_secrets.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return _cfg


def twiml(text=None, parts=None):
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?><Response>"
    for t in (parts or ([text] if text else [])):
        safe = t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        body += f"<Message>{safe}</Message>"
    body += "</Response>"
    return {"statusCode": 200, "headers": {"Content-Type": "text/xml"}, "body": body}


def valid_signature(url, params, signature):
    data = url + "".join(k + params[k] for k in sorted(params))
    digest = hmac.new(cfg()["twilio_auth_token"].encode(), data.encode(), hashlib.sha1).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def cognito_token():
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    c = cfg()
    basic = base64.b64encode(f'{c["cognito_client_id"]}:{c["cognito_client_secret"]}'.encode()).decode()
    req = urllib.request.Request(
        os.environ["COGNITO_DOMAIN"].rstrip("/") + "/oauth2/token",
        method="POST",
        data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": os.environ["COGNITO_SCOPE"]}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            t = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cognito token request failed HTTP {e.code}: {e.read().decode()[:300]}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"Cannot reach COGNITO_DOMAIN {os.environ['COGNITO_DOMAIN']}: {e.reason}") from None
    _token.update(value=t["access_token"], exp=time.time() + int(t.get("expires_in", 3600)))
    return _token["value"]


def ask_agent(prompt, actor_id, session_id):
    req = urllib.request.Request(
        runtime_url(),
        method="POST",
        data=json.dumps({"prompt": CHANNEL_NOTE + prompt, "actor_id": actor_id}).encode(),
        headers={
            "Authorization": f"Bearer {cognito_token()}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    with urllib.request.urlopen(req, timeout=280) as r:
        text = r.read().decode()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    _last_agent_info.update(gateway_status=data.get("gateway_status"), tools_available=data.get("tools_available"))
    return data.get("response") or text


def to_whatsapp(md):
    """Convert the agent's markdown into WhatsApp formatting."""
    t = md.replace("\r", "")
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r"\1: \2", t)
    t = re.sub(r"^#{1,6}\s*(.+)$", r"*\1*", t, flags=re.M)
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t)
    t = re.sub(r"^\s*[-*]\s+", "• ", t, flags=re.M)
    t = re.sub(r"^\|?\s*:?-{3,}.*$", "", t, flags=re.M)
    t = re.sub(r"^\|(.+)\|\s*$", lambda m: " · ".join(c.strip() for c in m.group(1).split("|")), t, flags=re.M)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def split_message(text):
    parts, current = [], ""
    for para in text.split("\n\n"):
        while len(para) > WA_LIMIT:
            parts.append(para[:WA_LIMIT]); para = para[WA_LIMIT:]
        if len(current) + len(para) + 2 > WA_LIMIT:
            parts.append(current); current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        parts.append(current)
    if len(parts) > 1:
        parts = [f"{p}\n\n({i}/{len(parts)})" for i, p in enumerate(parts, 1)]
    return parts


def send_whatsapp(to, text):
    c = cfg()
    url = f'https://api.twilio.com/2010-04-01/Accounts/{c["twilio_account_sid"]}/Messages.json'
    basic = base64.b64encode(f'{c["twilio_account_sid"]}:{c["twilio_auth_token"]}'.encode()).decode()
    for part in split_message(text):
        req = urllib.request.Request(
            url, method="POST",
            data=urllib.parse.urlencode({"From": os.environ["WHATSAPP_FROM"], "To": to, "Body": part}).encode(),
            headers={"Authorization": f"Basic {basic}", "Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            r.read()
        time.sleep(0.4)


def handle_webhook(event):
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    params = {k: v[0] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    if not valid_signature(os.environ["WEBHOOK_URL"].strip(), params, headers.get("x-twilio-signature")):
        print("Rejected: bad Twilio signature")
        return {"statusCode": 403, "body": "Forbidden"}

    sender = params.get("From", "")
    if not sender.startswith("whatsapp:"):
        return twiml()
    text = (params.get("Body") or "").strip()
    media = int(params.get("NumMedia", "0") or 0)
    if media and not text:
        text = "[The traveller sent a photo, file or voice note with no text. You cannot see or hear it.]"
    elif media:
        text += "\n\n[The traveller also attached a photo, file or voice note that you cannot see or hear.]"
    if not text:
        text = "[The traveller sent an empty message.]"

    fast_seconds = float(os.environ.get("FAST_REPLY_SECONDS", "11"))
    if fast_seconds > 0:
        try:
            actor_id, session_id, prompt = prepare_turn(sender, text)
            future = _pool.submit(ask_agent, prompt, actor_id, session_id)
            answer = future.result(timeout=fast_seconds)
            if str(answer).strip():
                parts = split_message(to_whatsapp(answer))
                print(f"FAST REPLY OK: agent replied in time ({len(str(answer))} characters, {len(parts)} part(s))")
                return twiml(parts=parts[:10])
        except FutureTimeout:
            print(f"FAST REPLY: agent still working after {fast_seconds}s, finishing in the background")
        except Exception as exc:
            print(f"FAST REPLY FAILED: {exc!r}; trying in the background")

    try:
        _lambda.invoke(
            FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"],
            InvocationType="Event",
            Payload=json.dumps({"async_job": {"from": sender, "text": text[:4000], "sid": params.get("MessageSid"),
                                              "continue": fast_seconds > 0}}).encode(),
        )
        print(f"STEP 1 OK: message received, background run started (MessageSid {params.get('MessageSid')})")
    except Exception as exc:
        print(f"STEP 1 FAILED: could not start the background run (lambda:InvokeFunction on itself): {exc!r}")
    return twiml()


def prepare_turn(sender, text):
    """Returns (actor_id, session_id, prompt) for one incoming message."""
    actor_id = "wa-" + hashlib.sha256(sender.encode()).hexdigest()[:32]
    if text.lower().strip(" .!") in RESET_WORDS:
        session_id = current_session(actor_id, force_new=True)
        text = ("[The traveller asked to start a new trip conversation. Confirm briefly and ask where "
                "they are going.]")
    else:
        session_id = current_session(actor_id)
    return actor_id, session_id, text


def current_session(actor_id, force_new=False):
    """Short-term memory session for this traveller: new after 'new trip' or 6 hours idle."""
    now = int(time.time())
    item = sessions_table().get_item(Key={"actor_id": actor_id}).get("Item") or {}
    if force_new or not item or now - int(item.get("last_seen", 0)) > IDLE_SECONDS:
        session_id = hashlib.sha256(f"{actor_id}:{uuid.uuid4()}".encode()).hexdigest()
    else:
        session_id = item["session_id"]
    sessions_table().put_item(Item={"actor_id": actor_id, "session_id": session_id, "last_seen": now,
                             "expires_at": now + 90 * 24 * 3600})
    return session_id


RESET_WORDS = ("new trip", "reset", "start over")


def handle_job(job):
    sender = job["from"]
    actor_id = "wa-" + hashlib.sha256(sender.encode()).hexdigest()[:32]
    print(f"STEP 2: background run started for {actor_id}")
    text = job["text"]
    try:
        if job.get("continue"):
            session_id = current_session(actor_id)
            if text.lower().strip(" .!") in RESET_WORDS:
                text = ("[The traveller asked to start a new trip conversation. Confirm briefly and ask "
                        "where they are going.]")
        else:
            actor_id, session_id, text = prepare_turn(sender, text)
        print("STEP 3 OK: conversation session ready (DynamoDB)")
    except Exception as exc:
        print(f"STEP 3 FAILED: DynamoDB session table: {exc!r}")
        return

    try:
        cognito_token()
        print("STEP 4 OK: Cognito access token received")
    except Exception as exc:
        print(f"STEP 4 FAILED: {exc}")
        return

    for attempt in (1, 2):
        try:
            print(f"STEP 5: calling the agent (attempt {attempt})")
            answer = ask_agent(text, actor_id, session_id)
            if not str(answer).strip():
                raise ValueError("agent returned an empty response")
            print(f"STEP 5 OK: agent replied ({len(str(answer))} characters)")
            break
        except urllib.error.HTTPError as e:
            print(f"STEP 5 FAILED: AGENT CALL FAILED HTTP {e.code}: {e.read().decode()[:800]}")
            if e.code in (400, 401, 403, 404):
                return
        except Exception as exc:
            print(f"STEP 5 FAILED: AGENT CALL FAILED: {exc!r}")
        time.sleep(3)
    else:
        return

    try:
        send_whatsapp(sender, to_whatsapp(answer))
        print("STEP 6 OK: reply sent through Twilio")
    except urllib.error.HTTPError as e:
        print(f"STEP 6 FAILED: Twilio send HTTP {e.code}: {e.read().decode()[:500]}")
    except Exception as exc:
        print(f"STEP 6 FAILED: Twilio send: {exc!r}")


def self_check(test_agent=False):
    """Opened in a browser (GET): shows which settings are missing. Never shows secret values."""
    checks = {"missing_environment_variables": [v for v in REQUIRED_ENV if not os.environ.get(v, "").strip()]}
    try:
        c = cfg()
        checks["secret"] = "readable"
        checks["secret_missing_keys"] = [k for k in SECRET_KEYS if not str(c.get(k, "")).strip()]
    except Exception as exc:
        checks["secret"] = f"ERROR {type(exc).__name__}: {str(exc)[:200]}"
    try:
        sessions_table().get_item(Key={"actor_id": "health-check"})
        checks["sessions_table"] = "reachable"
    except Exception as exc:
        checks["sessions_table"] = f"ERROR {type(exc).__name__}: {str(exc)[:200]}"
    try:
        runtime_url()
        checks["agent_runtime_arn"] = "looks valid"
    except Exception as exc:
        checks["agent_runtime_arn"] = f"ERROR {type(exc).__name__}: {exc}"
    checks["webhook_url_ends_with_slash"] = os.environ.get("WEBHOOK_URL", "").endswith("/")
    try:
        _lambda.invoke(FunctionName=os.environ["AWS_LAMBDA_FUNCTION_NAME"], InvocationType="DryRun")
        checks["self_invoke_permission"] = "ok"
    except Exception as exc:
        checks["self_invoke_permission"] = f"ERROR {type(exc).__name__}: {str(exc)[:200]}"
    try:
        cognito_token()
        checks["cognito_token"] = "ok"
    except Exception as exc:
        checks["cognito_token"] = f"ERROR {str(exc)[:300]}"
    if test_agent:
        try:
            reply = ask_agent("Say hello to the Airxpact team in one short sentence.", "wa-healthcheck",
                              hashlib.sha256(b"wa-healthcheck").hexdigest())
            checks["agent_test_reply"] = str(reply)[:500]
            checks["agent_gateway_status"] = _last_agent_info.get("gateway_status")
            checks["agent_tools_available"] = _last_agent_info.get("tools_available")
        except urllib.error.HTTPError as e:
            checks["agent_test_reply"] = f"ERROR HTTP {e.code}: {e.read().decode()[:500]}"
        except Exception as exc:
            checks["agent_test_reply"] = f"ERROR {type(exc).__name__}: {str(exc)[:300]}"
    ok = (not checks["missing_environment_variables"] and checks["secret"] == "readable"
          and not checks.get("secret_missing_keys") and checks["sessions_table"] == "reachable"
          and checks["agent_runtime_arn"] == "looks valid" and checks["self_invoke_permission"] == "ok"
          and checks["cognito_token"] == "ok"
          and not str(checks.get("agent_test_reply", "")).startswith("ERROR"))
    checks["status"] = "READY - point Twilio here with method POST" if ok else "NOT READY - fix the items above"
    if not test_agent:
        checks["tip"] = "Add ?test=agent to the end of this URL to send a real test message to the agent."
    print("Self-check:", json.dumps(checks))
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(checks, indent=2)}


def lambda_handler(event, context):
    try:
        if "async_job" in event:
            handle_job(event["async_job"])
            return {"ok": True}
        if event.get("requestContext", {}).get("http", {}).get("method") == "GET":
            q = event.get("queryStringParameters") or {}
            return self_check(test_agent=q.get("test") == "agent")
        return handle_webhook(event)
    except KeyError as exc:
        print(f"CONFIG ERROR: environment variable or secret key {exc} is missing")
        raise
    except Exception as exc:
        print(f"UNHANDLED ERROR {type(exc).__name__}: {exc}")
        raise
