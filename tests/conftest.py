import importlib.util
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("MODEL_ID", "test-model")
os.environ.setdefault("AGENT_RUNTIME_ARN", "arn:aws:bedrock-agentcore:us-east-1:111122223333:runtime/airxpact_agent-test")


def load(relative_path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def tools():
    return load("functions/aviation_tools/lambda_function.py", "aviation_tools")


@pytest.fixture(scope="session")
def eum():
    return load("functions/whatsapp_eum/lambda_function.py", "whatsapp_eum")


@pytest.fixture(scope="session")
def twilio():
    return load("functions/whatsapp_twilio/lambda_function.py", "whatsapp_twilio")


@pytest.fixture(scope="session")
def api():
    return load("functions/api/lambda_function.py", "api")


@pytest.fixture(scope="session")
def agent():
    return load("agent/agent.py", "airxpact_agent")
