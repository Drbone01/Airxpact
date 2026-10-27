"""HTTPS entry point for web and partner clients. Forwards authenticated requests to AgentCore Runtime."""
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
REGION = RUNTIME_ARN.split(":")[3]
QUALIFIER = os.environ.get("QUALIFIER", "DEFAULT")
RUNTIME_URL = (
    f"https://bedrock-agentcore.{REGION}.amazonaws.com/runtimes/"
    f"{urllib.parse.quote(RUNTIME_ARN, safe='')}/invocations?qualifier={QUALIFIER}"
)


def _resp(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}


def _claims(token):
    """Read (not verify) JWT claims. AgentCore Runtime verifies the signature on the same token."""
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return {}


def _safe(value):
    return re.sub(r"[^a-zA-Z0-9\-_]", "-", str(value or ""))[:64]


def lambda_handler(event, context):
    if event.get("requestContext", {}).get("http", {}).get("method") != "POST":
        return _resp(405, {"error": "Use POST"})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return _resp(401, {"error": "Missing Bearer token"})
    token = auth.split(" ", 1)[1].strip()

    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return _resp(400, {"error": "Body must be JSON"})
    prompt = str(body.get("prompt", "")).strip()
    if not prompt or len(prompt) > 8000:
        return _resp(400, {"error": "prompt is required (max 8000 characters)"})

    c = _claims(token)
    client_id = c.get("client_id", "")
    sub = c.get("sub", "")
    if sub and sub != client_id:
        actor_id = f"user-{_safe(sub)}"
    else:
        actor_id = f"partner-{_safe(client_id)}-{_safe(body.get('user_id') or 'default')}"

    client_session = _safe(body.get("session_id") or "default")
    session_id = hashlib.sha256(f"{actor_id}:{client_session}".encode()).hexdigest()

    req = urllib.request.Request(
        RUNTIME_URL,
        method="POST",
        data=json.dumps({"prompt": prompt, "actor_id": actor_id}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=290) as r:
            text = r.read().decode()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = {"response": text}
            data.pop("actor_id", None)
            data["session_id"] = client_session
            return _resp(200, data)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        print(f"Runtime error {e.code}: {detail}")
        return _resp(e.code if e.code in (400, 401, 403, 404, 429) else 502, {"error": "Agent call failed", "status": e.code, "detail": detail})
    except Exception as exc:
        print(f"Runtime call failed: {exc!r}")
        return _resp(504, {"error": "The agent took too long or could not be reached. Please try again."})
