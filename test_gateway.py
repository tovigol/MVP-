import asyncio

import pytest
from fastmcp import Client

import gateway
from dev.mock_email_server import mcp as mock_server


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
    monkeypatch.setattr(gateway, "get_http_headers", lambda include_all=False: headers)

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
