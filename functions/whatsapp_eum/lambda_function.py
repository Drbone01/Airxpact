"""WhatsApp channel through AWS End User Messaging Social: SNS events in, SendWhatsAppMessage out."""
import base64
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import boto3

WA_LIMIT = 3800
IDLE_SECONDS = 6 * 3600
RESET_WORDS = ("new trip", "reset", "start over")
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

_social = boto3.client("socialmessaging")
_secrets = boto3.client("secretsmanager")
_dynamodb = boto3.resource("dynamodb")
_cfg = None
_token = {"value": None, "exp": 0}


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or not str(value).strip():
        raise KeyError(name)
    return str(value).strip()


def table():
    return _dynamodb.Table(env("SESSIONS_TABLE"))


def cfg():
    global _cfg
    if _cfg is None:
        _cfg = json.loads(_secrets.get_secret_value(SecretId=env("SECRET_ARN"))["SecretString"])
    return _cfg


def runtime_url():
    arn = env("AGENT_RUNTIME_ARN")
    return (f"https://bedrock-agentcore.{arn.split(':')[3]}.amazonaws.com/runtimes/"
            f"{urllib.parse.quote(arn, safe='')}/invocations?qualifier={env('QUALIFIER', 'DEFAULT')}")


def cognito_token():
    if _token["value"] and _token["exp"] > time.time() + 60:
        return _token["value"]
    c = cfg()
    basic = base64.b64encode(f'{c["cognito_client_id"]}:{c["cognito_client_secret"]}'.encode()).decode()
    req = urllib.request.Request(
        env("COGNITO_DOMAIN").rstrip("/") + "/oauth2/token", method="POST",
        data=urllib.parse.urlencode({"grant_type": "client_credentials", "scope": env("COGNITO_SCOPE")}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Authorization": f"Basic {basic}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            t = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Cognito token request failed HTTP {e.code}: {e.read().decode()[:300]}") from None
    _token.update(value=t["access_token"], exp=time.time() + int(t.get("expires_in", 3600)))
    return _token["value"]


def ask_agent(prompt, actor_id, session_id):
    req = urllib.request.Request(
        runtime_url(), method="POST",
        data=json.dumps({"prompt": CHANNEL_NOTE + prompt, "actor_id": actor_id}).encode(),
        headers={"Authorization": f"Bearer {cognito_token()}", "Content-Type": "application/json",
                 "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id": session_id},
    )
    with urllib.request.urlopen(req, timeout=280) as r:
        text = r.read().decode()
    try:
        return json.loads(text).get("response") or text
    except json.JSONDecodeError:
        return text


def to_whatsapp(md):
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


def send_raw(phone_number_id, payload):
    _social.send_whatsapp_message(
        originationPhoneNumberId=phone_number_id,
        metaApiVersion=os.environ.get("META_API_VERSION", "v20.0").strip() or "v20.0",
        message=json.dumps(payload).encode(),
    )


def send_text(phone_number_id, to, text):
    to = "+" + re.sub(r"\D", "", str(to))
    for part in split_message(text):
        send_raw(phone_number_id, {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                                   "type": "text", "text": {"preview_url": True, "body": part}})
        time.sleep(0.3)


def first_time(message_id):
    """True the first time we see this WhatsApp message id (SNS can deliver twice)."""
    try:
        table().put_item(
            Item={"actor_id": f"msg#{message_id}", "expires_at": int(time.time()) + 3 * 86400},
            ConditionExpression="attribute_not_exists(actor_id)",
        )
        return True
    except _dynamodb.meta.client.exceptions.ConditionalCheckFailedException:
        return False


def current_session(actor_id, force_new=False):
    now = int(time.time())
    item = table().get_item(Key={"actor_id": actor_id}).get("Item") or {}
    if force_new or not item or now - int(item.get("last_seen", 0)) > IDLE_SECONDS:
        session_id = hashlib.sha256(f"{actor_id}:{uuid.uuid4()}".encode()).hexdigest()
    else:
        session_id = item["session_id"]
    table().put_item(Item={"actor_id": actor_id, "session_id": session_id, "last_seen": now,
                           "expires_at": now + 90 * 24 * 3600})
    return session_id


def message_text(msg):
    kind = msg.get("type")
    if kind == "text":
        return (msg.get("text") or {}).get("body", "").strip()
    if kind == "button":
        return (msg.get("button") or {}).get("text", "").strip()
    if kind == "interactive":
        i = msg.get("interactive") or {}
        return ((i.get("button_reply") or i.get("list_reply") or {}).get("title") or "").strip()
    if kind == "location":
        loc = msg.get("location") or {}
        return f"[The traveller shared a location: {loc.get('name') or ''} {loc.get('address') or ''} " \
               f"lat {loc.get('latitude')}, lon {loc.get('longitude')}]"
    caption = ((msg.get(kind) or {}) if isinstance(msg.get(kind), dict) else {}).get("caption", "")
    note = f"[The traveller sent a {kind} message that you cannot see or hear.]"
    return f"{caption}\n\n{note}".strip() if caption else note


def handle_message(phone_number_id, msg):
    wa_from = msg.get("from", "")
    message_id = msg.get("id", "")
    if not wa_from or not message_id:
        return
    if not first_time(message_id):
        print(f"SKIP duplicate {message_id}")
        return
    actor_id = "wa-" + hashlib.sha256(f"whatsapp:+{wa_from.lstrip('+')}".encode()).hexdigest()[:32]
    print(f"STEP 1 OK: WhatsApp message {message_id} for {actor_id}")

    try:
        send_raw(phone_number_id, {"messaging_product": "whatsapp", "status": "read", "message_id": message_id})
    except Exception as exc:
        print(f"NOTE: could not mark as read: {exc!r}")

    text = message_text(msg) or "[The traveller sent an empty message.]"
    try:
        if text.lower().strip(" .!") in RESET_WORDS:
            session_id = current_session(actor_id, force_new=True)
            text = ("[The traveller asked to start a new trip conversation. Confirm briefly and ask where "
                    "they are going.]")
        else:
            session_id = current_session(actor_id)
        print("STEP 2 OK: conversation session ready (DynamoDB)")
    except Exception as exc:
        print(f"STEP 2 FAILED: DynamoDB session table: {exc!r}")
        return

    try:
        cognito_token()
        print("STEP 3 OK: Cognito access token received")
    except Exception as exc:
        print(f"STEP 3 FAILED: {exc}")
        return

    answer = None
    for attempt in (1, 2):
        try:
            print(f"STEP 4: calling the agent (attempt {attempt})")
            answer = ask_agent(text[:4000], actor_id, session_id)
            if str(answer).strip():
                print(f"STEP 4 OK: agent replied ({len(str(answer))} characters)")
                break
            raise ValueError("agent returned an empty response")
        except urllib.error.HTTPError as e:
            print(f"STEP 4 FAILED: AGENT CALL FAILED HTTP {e.code}: {e.read().decode()[:800]}")
            if e.code in (400, 401, 403, 404):
                return
        except Exception as exc:
            print(f"STEP 4 FAILED: AGENT CALL FAILED: {exc!r}")
        answer = None
        time.sleep(3)
    if not answer:
        return

    try:
        send_text(phone_number_id, wa_from, to_whatsapp(str(answer)))
        print("STEP 5 OK: reply sent through AWS End User Messaging Social")
    except Exception as exc:
        print(f"STEP 5 FAILED: SendWhatsAppMessage to +{re.sub(r'[^0-9]', '', wa_from)[:5]}...: {exc!r}")


def lambda_handler(event, context):
    for record in event.get("Records", []):
        try:
            notice = json.loads(record["Sns"]["Message"])
            phone_ids = (notice.get("context") or {}).get("MetaPhoneNumberIds") or []
            phone_number_id = phone_ids[0]["arn"] if phone_ids else None
            entry = notice.get("whatsAppWebhookEntry")
            entry = json.loads(entry) if isinstance(entry, str) else (entry or {})
        except Exception as exc:
            print(f"SKIP: not a WhatsApp event: {exc!r}")
            continue
        for change in entry.get("changes", []):
            for msg in (change.get("value") or {}).get("messages", []) or []:
                if not phone_number_id:
                    print("SKIP: event has no MetaPhoneNumberIds")
                    continue
                try:
                    handle_message(phone_number_id, msg)
                except KeyError as exc:
                    print(f"CONFIG ERROR: environment variable or secret key {exc} is missing")
                except Exception as exc:
                    print(f"UNHANDLED ERROR {type(exc).__name__}: {exc}")
    return {"ok": True}
