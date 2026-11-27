import asyncio

import pytest
from fastmcp import Client

import json

import gateway
from dev import mock_email_server
from dev.mock_email_server import mcp as mock_server
from shared import policy, store


def run(coro):
    return asyncio.run(coro)


def test_lists_prefixed_tools():
    async def go():
        async with Client(gateway.build_gateway(mock_server)) as c:
            return [t.name for t in await c.list_tools()]

    assert "email_send_email" in run(go())


def _call(token):
    async def go():
        gw = gateway.build_gateway(mock_server, token=token)
        async with Client(gw) as c:
            return await c.call_tool("email_send_email", {"to": "a@b.com"})

    return go


def test_token_required_when_set(monkeypatch):
    from fastmcp.exceptions import ToolError

    headers = {}
    monkeypatch.setattr(gateway, "get_http_headers", lambda include_all=False: headers if include_all else {})

    async def passthrough(ctx):
        return "ok"

    guard = gateway.Guard("s3cret")
    # Guard itself raises Unauthorized for a missing or wrong header...
    for h in ({}, {"authorization": "Bearer wrong"}, {"authorization": "s3cret"}):
        headers.clear()
        headers.update(h)
        with pytest.raises(ToolError, match="Unauthorized"):
            run(guard.on_request(None, passthrough))
    # ...and a full client session is rejected end to end (the client may
    # surface the failure as a handshake error rather than the original text).
    headers.clear()
    with pytest.raises(Exception):
        run(_call("s3cret")())

    # Correct header: the call goes through.
    headers["authorization"] = "Bearer s3cret"
    assert run(guard.on_request(None, passthrough)) == "ok"
    result = run(_call("s3cret")())
    assert "sent to 1 recipients" in result.content[0].text


def test_refuses_public_bind_without_token():
    with pytest.raises(SystemExit):
        gateway.check_bind("0.0.0.0", None)
    with pytest.raises(SystemExit):
        gateway.check_bind("0.0.0.0", "")
    gateway.check_bind("0.0.0.0", "tok")
    gateway.check_bind("127.0.0.1", None)


@pytest.fixture
def tmp_guard(tmp_path, monkeypatch):
    registry = {
        "email_send_email": {"category": "email_send", "risk": "write",
                             "recipient_fields": ["to", "cc", "bcc"]},
        "email_fail": {"category": "payment", "risk": "write",
                       "amount_field": "amount", "amount_unit": "usd"},
    }
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(registry))
    pol = tmp_path / "policy.json"
    pol.write_text("{}")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "guard.db")
    monkeypatch.setattr(policy, "REGISTRY_PATH", reg)
    monkeypatch.setattr(policy, "POLICY_PATH", pol)
    mock_email_server.CALLS.clear()
    yield
    mock_email_server.CALLS.clear()


def test_call_is_logged(tmp_guard):
    run(_call(None)())
    event = store.recent_events()[0]
    assert event["tool"] == "email_send_email"
    assert event["outcome"] == "forwarded"
    assert event["category"] == "email_send"
    assert event["agent"] == "agent"
    assert len(mock_email_server.CALLS) == 1


def test_upstream_error_logged_as_error(tmp_guard):
    from fastmcp.exceptions import ToolError

    async def go():
        async with Client(gateway.build_gateway(mock_server)) as c:
            await c.call_tool("email_fail", {"amount": 50})

    with pytest.raises(ToolError):
        run(go())
    event = store.recent_events()[0]
    assert event["tool"] == "email_fail"
    assert event["outcome"] == "error"
    assert event["amount_usd"] == 50
    assert store.daily_payment_total() == 0


def test_registry_failure_denies_without_forwarding(tmp_guard, monkeypatch):
    from fastmcp.exceptions import ToolError

    monkeypatch.setattr(policy, "REGISTRY_PATH", policy.REGISTRY_PATH.parent / "missing.json")
    with pytest.raises(ToolError):
        run(_call(None)())
    assert mock_email_server.CALLS == []
