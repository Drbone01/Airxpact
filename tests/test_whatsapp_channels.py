import base64
import hashlib
import hmac
import json
import urllib.parse


def test_markdown_becomes_whatsapp_text(eum):
    text = eum.to_whatsapp("## Plan\n**Nonstop** $864\n- Free carry-on\n[Book](https://example.com/b)")
    assert text == "*Plan*\n*Nonstop* $864\n• Free carry-on\nBook: https://example.com/b"


def test_long_messages_are_split_and_numbered(eum):
    parts = eum.split_message(("word " * 900 + "\n\n") * 2)
    assert len(parts) > 1 and parts[-1].endswith(f"({len(parts)}/{len(parts)})")
    assert all(len(p) <= eum.WA_LIMIT + 10 for p in parts)


def test_eum_sends_in_e164_format(eum, monkeypatch):
    sent = []
    monkeypatch.setattr(eum, "send_raw", lambda phone_id, payload: sent.append(payload))
    eum.send_text("arn:aws:social-messaging:us-east-1:111122223333:phone-number-id/abc", "2348012345678", "hi")
    assert sent[0]["to"] == "+2348012345678"


def test_eum_handles_sns_event_end_to_end(eum, monkeypatch):
    sent = []
    monkeypatch.setattr(eum, "first_time", lambda message_id: True)
    monkeypatch.setattr(eum, "current_session", lambda actor_id, force_new=False: "s" * 64)
    monkeypatch.setattr(eum, "cognito_token", lambda: "token")
    monkeypatch.setattr(eum, "ask_agent", lambda prompt, actor, session: "**Hello** from Airxpact")
    monkeypatch.setattr(eum, "send_raw", lambda phone_id, payload: sent.append(payload))
    entry = {"changes": [{"value": {"messages": [
        {"from": "2348012345678", "id": "wamid.1", "type": "text", "text": {"body": "hi"}}]}}]}
    notice = {"context": {"MetaPhoneNumberIds": [{"arn": "arn:aws:social-messaging:us-east-1:111122223333:phone-number-id/abc"}]},
              "whatsAppWebhookEntry": json.dumps(entry)}
    eum.lambda_handler({"Records": [{"Sns": {"Message": json.dumps(notice)}}]}, None)
    assert sent[0]["status"] == "read"
    assert sent[1]["text"]["body"] == "*Hello* from Airxpact"


def test_same_traveller_has_same_memory_id_on_both_channels(eum, twilio):
    twilio_actor = "wa-" + hashlib.sha256("whatsapp:+2348012345678".encode()).hexdigest()[:32]
    eum_actor = "wa-" + hashlib.sha256(f"whatsapp:+{'2348012345678'}".encode()).hexdigest()[:32]
    assert twilio_actor == eum_actor


def test_twilio_signature_validation(twilio, monkeypatch):
    monkeypatch.setattr(twilio, "_cfg", {"twilio_auth_token": "secret"})
    url, params = "https://example.lambda-url.us-east-1.on.aws/", {"Body": "hi", "From": "whatsapp:+234"}
    data = url + "".join(k + params[k] for k in sorted(params))
    good = base64.b64encode(hmac.new(b"secret", data.encode(), hashlib.sha1).digest()).decode()
    assert twilio.valid_signature(url, params, good)
    assert not twilio.valid_signature(url, params, "forged")


def test_twilio_rejects_unsigned_webhook(twilio, monkeypatch):
    monkeypatch.setattr(twilio, "_cfg", {"twilio_auth_token": "secret"})
    monkeypatch.setenv("WEBHOOK_URL", "https://example.lambda-url.us-east-1.on.aws/")
    event = {"requestContext": {"http": {"method": "POST"}}, "headers": {"X-Twilio-Signature": "forged"},
             "body": urllib.parse.urlencode({"Body": "hi", "From": "whatsapp:+234"})}
    assert twilio.lambda_handler(event, None)["statusCode"] == 403
