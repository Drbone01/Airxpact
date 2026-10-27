import base64
import json


def token(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def test_requires_bearer_token(api):
    event = {"requestContext": {"http": {"method": "POST"}}, "headers": {}, "body": "{}"}
    assert api.lambda_handler(event, None)["statusCode"] == 401


def test_rejects_missing_prompt(api):
    event = {"requestContext": {"http": {"method": "POST"}},
             "headers": {"authorization": "Bearer " + token({"sub": "u1", "client_id": "c1"})}, "body": "{}"}
    assert api.lambda_handler(event, None)["statusCode"] == 400


def test_reads_claims_without_network(api):
    assert api._claims(token({"sub": "abc", "client_id": "c1"}))["sub"] == "abc"
