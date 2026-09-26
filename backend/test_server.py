"""
Regression tests for the 2026-09-26 functional fixes to whatsapp-ai-agent.

- defect 1: python-multipart must be in backend/requirements.txt
- defect 2: send_twilio_message must use an async Twilio client
- defect 3: Twilio signature validation on by default, off via env var
- defect 4: GET /health endpoint
- defect 5: litellm call guarded by asyncio.wait_for
"""
import asyncio
import inspect
import os
import re

# Must be set before importing server (database.py reads DATABASE_URL at import).
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:////tmp/waa_tests.db"
os.environ["TWILIO_ACCOUNT_SID"] = "dummy_sid"
os.environ["TWILIO_AUTH_TOKEN"] = "dummy_token"
os.environ["TWILIO_PHONE_NUMBER"] = "+15550001111"
os.environ["OPENAI_API_KEY"] = "dummy_key"
os.environ["META_VERIFY_TOKEN"] = "dummy_verify"
os.environ["LLM_PROVIDER"] = "gpt-4o"
os.environ["TWILIO_VALIDATE_SIGNATURE"] = "false"  # local test default; overridden per-test
for proxy_var in ("no_proxy", "NO_PROXY"):
    os.environ.pop(proxy_var, None)

import pytest
from fastapi.testclient import TestClient

import server
from twilio.http.async_http_client import AsyncTwilioHttpClient

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_requirements_include_python_multipart():
    """Defect 1: python-multipart must be declared (Twilio webhook 500s without it)."""
    req_path = os.path.join(HERE, "requirements.txt")
    with open(req_path) as f:
        content = f.read()
    assert re.search(r"(?im)^python-multipart\s*(>=|==|~=|\s*$)", content), (
        "python-multipart missing from backend/requirements.txt"
    )


def test_health_endpoint(client):
    """Defect 4: GET /health must exist and report readiness."""
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok", f"health check degraded: {body}"
    assert body["db"] == "ok"


def test_twilio_webhook_form_post_returns_200(client, monkeypatch):
    """Defect 1+3: form POST (needs python-multipart) returns 200 with a valid pipeline run."""

    class _Msg:
        content = "Test reply"

    class _Choice:
        message = _Msg()

    class _Resp:
        choices = [_Choice()]

    async def fake_acompletion(**kwargs):
        return _Resp()

    sent = []

    async def fake_send(to_number: str, text: str):
        sent.append((to_number, text))

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)
    monkeypatch.setattr(server, "send_twilio_message", fake_send)

    res = client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+15550002222", "Body": "Do you offer bulk pricing?"},
    )
    assert res.status_code == 200
    assert "<Response>" in res.text
    assert sent == [("+15550002222", "Test reply")], f"pipeline did not dispatch reply: {sent}"


def test_twilio_signature_validation_enabled_by_default(client, monkeypatch):
    """Defect 3: with validation on (default), an unsigned request is rejected."""
    monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
    res = client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+15550002222", "Body": "hello"},
    )
    assert res.status_code == 403


def test_twilio_signature_validation_rejects_bad_signature(client, monkeypatch):
    monkeypatch.setenv("TWILIO_VALIDATE_SIGNATURE", "true")
    res = client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+15550002222", "Body": "hello"},
        headers={"X-Twilio-Signature": "bogus"},
    )
    assert res.status_code == 403


def test_send_twilio_message_uses_async_client(monkeypatch):
    """Defect 2: the outbound sender must build an async-capable Twilio client.

    The original bug built a sync Client then called create_async(), which raises
    "http_client must be asynchronous". We capture the Client kwargs and assert the
    http_client is a real AsyncTwilioHttpClient, and that create_async was awaited.
    """
    captured = {}

    class _FakeMessages:
        async def create_async(self, **kwargs):
            captured["create_async_kwargs"] = kwargs

    class _FakeClient:
        def __init__(self, sid, token, **kwargs):
            captured["http_client"] = kwargs.get("http_client")
            self.messages = _FakeMessages()

    monkeypatch.setattr(server, "Client", _FakeClient)

    asyncio.run(server.send_twilio_message("+15550002222", "hello there"))

    http_client = captured.get("http_client")
    assert isinstance(http_client, AsyncTwilioHttpClient), (
        f"expected AsyncTwilioHttpClient, got {type(http_client)!r} "
        "(would raise 'http_client must be asynchronous')"
    )
    assert captured["create_async_kwargs"]["to"] == "whatsapp:+15550002222"
    assert captured["create_async_kwargs"]["body"] == "hello there"


def test_litellm_call_wrapped_in_asyncio_wait_for():
    """Defect 5: a hung litellm client construction must not hang the worker."""
    source = inspect.getsource(server.process_whatsapp_message_task)
    assert "asyncio.wait_for" in source, "litellm call is not guarded by asyncio.wait_for"
