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
            return await c.call_tool("email_send_email", {"to": "bob@yossibakery.com"})

    return go


def test_token_required_when_set(tmp_guard, monkeypatch):
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


POLICY = {
    "company_domains": ["yossibakery.com"],
    "default_by_risk": {"read": "allow", "write": "ask", "destructive": "deny"},
    "rules": [
        {"id": "internal-email", "category": "email_send", "effect": "allow",
         "when": {"all_recipients_in": "company_domains"}, "explain": "Internal email"},
        {"id": "small-payment", "category": "payment", "effect": "allow",
         "when": {"amount_usd_lte": 100}, "explain": "Small payment"},
    ],
}


@pytest.fixture
def tmp_guard(tmp_path, monkeypatch):
    registry = {
        "email_send_email": {"category": "email_send", "risk": "write",
                             "recipient_fields": ["to", "cc", "bcc"]},
        "email_fail": {"category": "payment", "risk": "write",
                       "amount_field": "amount", "amount_unit": "usd"},
        "email_export_all": {"category": "bulk_export", "risk": "destructive"},
        "email_pay": {"category": "payment", "risk": "write",
                      "amount_field": "amount", "amount_unit": "cents"},
    }
    reg = tmp_path / "registry.json"
    reg.write_text(json.dumps(registry))
    pol = tmp_path / "policy.json"
    pol.write_text(json.dumps(POLICY))
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
    with pytest.raises(ToolError, match="Blocked by policy: Policy check failed"):
        run(_call(None)())
    assert mock_email_server.CALLS == []


def _call_raw(tool, args):
    async def go():
        async with Client(gateway.build_gateway(mock_server)) as c:
            return await c.call_tool(tool, args, raise_on_error=False)

    return run(go())


def test_export_denied_upstream_not_called(tmp_guard):
    r = _call_raw("email_export_all", {})
    assert r.is_error
    assert r.content[0].text.startswith("Blocked by policy:")
    assert not r.content[0].text.count("..")
    assert store.recent_events()[0]["outcome"] == "blocked"
    assert mock_email_server.CALLS == []


def test_internal_email_allowed(tmp_guard):
    r = _call_raw("email_send_email", {"to": "bob@yossibakery.com"})
    assert not r.is_error
    assert store.recent_events()[0]["outcome"] == "forwarded"
    assert len(mock_email_server.CALLS) == 1



def test_broken_policy_file_denies(tmp_guard):
    policy.POLICY_PATH.write_text("{")
    r = _call_raw("email_send_email", {"to": "bob@yossibakery.com"})
    assert r.is_error
    assert r.content[0].text.startswith("Blocked by policy:")
    assert store.recent_events()[0]["outcome"] == "blocked"
    assert mock_email_server.CALLS == []
    policy.POLICY_PATH.write_text(json.dumps(POLICY))
    r = _call_raw("email_send_email", {"to": "bob@yossibakery.com"})
    assert not r.is_error
    assert store.recent_events()[0]["outcome"] == "forwarded"


# --- ask flow -------------------------------------------------------------------

def _decide_later(status, delay=0.3):
    async def go():
        await asyncio.sleep(delay)
        while not (rows := store.pending_approvals()):
            await asyncio.sleep(0.05)
        assert store.decide_approval(rows[0]["approval_id"], status, "cli")
    return go()


def _call_with_decider(tool, args, status):
    async def go():
        async with Client(gateway.build_gateway(mock_server)) as c:
            decider = asyncio.create_task(_decide_later(status))
            r = await c.call_tool(tool, args, raise_on_error=False)
            await decider
            return r
    return run(go())


def test_ask_approved_forwards(tmp_guard):
    r = _call_with_decider("email_send_email", {"to": "x@external.com"}, "approved")
    assert not r.is_error
    assert "sent to 1 recipients" in r.content[0].text
    event = store.recent_events()[0]
    assert event["effect"] == "ask"
    assert event["outcome"] == "approved"
    assert len(mock_email_server.CALLS) == 1


def test_ask_rejected(tmp_guard):
    r = _call_with_decider("email_send_email", {"to": "x@external.com"}, "rejected")
    assert r.is_error
    assert r.content[0].text == "The owner rejected this action."
    assert store.recent_events()[0]["outcome"] == "rejected"
    assert mock_email_server.CALLS == []


def test_ask_timeout(tmp_guard, monkeypatch):
    import re
    monkeypatch.setenv("GUARD_APPROVAL_TIMEOUT", "0.5")
    r = _call_raw("email_send_email", {"to": "x@external.com"})
    assert r.is_error
    m = re.fullmatch(r"Pending owner approval \(id (\w{8})\)\. Tell the user and stop\.",
                     r.content[0].text)
    assert m
    assert store.recent_events()[0]["outcome"] == "timeout"
    assert store.approval_status(m.group(1)) == "rejected"
    assert store.pending_approvals() == []
    assert mock_email_server.CALLS == []


def test_ask_cancelled_marks_timeout(tmp_guard, monkeypatch):
    monkeypatch.setenv("GUARD_APPROVAL_TIMEOUT", "30")
    guard = gateway.Guard()

    class Ctx:
        class message:
            name = "email_send_email"
            arguments = {"to": "x@external.com"}

    async def never(ctx):
        raise AssertionError("must not forward")

    async def go():
        task = asyncio.create_task(guard.on_call_tool(Ctx, never))
        while not store.pending_approvals():
            await asyncio.sleep(0.05)
        approval_id = store.pending_approvals()[0]["approval_id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return approval_id

    approval_id = run(go())
    assert store.recent_events()[0]["outcome"] == "timeout"
    assert store.approval_status(approval_id) == "rejected"


def test_concurrent_payments_respect_daily_limit(tmp_guard, monkeypatch):
    monkeypatch.setenv("GUARD_APPROVAL_TIMEOUT", "0")
    policy.POLICY_PATH.write_text(json.dumps({
        **POLICY,
        "rules": [{"id": "daily-cap", "category": "payment", "effect": "allow",
                   "when": {"daily_total_usd_lte": 300}, "explain": "Under daily cap"}],
    }))
    store.log_event("email_pay", {"amount": 19800}, policy.Decision("allow", "seed", "seed", "seed"),
                    "payment", 198, "agent")

    async def go():
        async with Client(gateway.build_gateway(mock_server)) as c:
            return await asyncio.gather(
                c.call_tool("email_pay", {"amount": 9900}, raise_on_error=False),
                c.call_tool("email_pay", {"amount": 9900}, raise_on_error=False))

    run(go())
    new = [e for e in store.recent_events() if e["agent"] == "agent" and e["amount_usd"] == 99]
    assert len(new) == 2
    assert sorted(e["effect"] for e in new) == ["allow", "ask"]
    assert sorted(e["outcome"] for e in new) == ["forwarded", "timeout"]
    assert len(mock_email_server.CALLS) == 1


def _ctx(tool, args):
    class Ctx:
        class message:
            name = tool
            arguments = args
    return Ctx


def _seed_payment_policy():
    policy.POLICY_PATH.write_text(json.dumps({
        **POLICY,
        "rules": [{"id": "daily-cap", "category": "payment", "effect": "allow",
                   "when": {"daily_total_usd_lte": 300}, "explain": "Under daily cap"}],
    }))
    store.log_event("email_pay", {"amount": 19800}, policy.Decision("allow", "seed", "seed", "seed"),
                    "payment", 198, "agent")


def test_concurrent_payments_direct_no_await_before_log(tmp_guard, monkeypatch):
    monkeypatch.setenv("GUARD_APPROVAL_TIMEOUT", "0")
    _seed_payment_policy()
    guard = gateway.Guard()
    forwarded = []

    async def fwd(ctx):
        forwarded.append(1)
        return "ok"

    async def go():
        return await asyncio.gather(
            guard.on_call_tool(_ctx("email_pay", {"amount": 9900}), fwd),
            guard.on_call_tool(_ctx("email_pay", {"amount": 9900}), fwd),
            return_exceptions=True)

    run(go())
    new = [e for e in store.recent_events() if e["amount_usd"] == 99]
    assert sorted(e["effect"] for e in new) == ["allow", "ask"]
    assert sorted(e["outcome"] for e in new) == ["forwarded", "timeout"]
    assert forwarded == [1]


def test_ask_store_error_cleans_up(tmp_guard, monkeypatch):
    async def boom(approval_id, timeout):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store, "wait_for_approval", boom)
    guard = gateway.Guard()

    async def never(ctx):
        raise AssertionError("must not forward")

    with pytest.raises(RuntimeError, match="locked"):
        run(guard.on_call_tool(_ctx("email_send_email", {"to": "x@external.com"}), never))
    event = store.recent_events()[0]
    assert event["outcome"] == "timeout"
    assert store.approval_status(_only_approval_id()) == "rejected"
    assert store.pending_approvals() == []


def _only_approval_id():
    with store.connect() as conn:
        return conn.execute("SELECT id FROM approvals").fetchone()["id"]


def test_startup_report_lists_unregistered():
    registry = {"email_send_email": {"category": "email"}}
    names = ["stripe_create_payment", "email_send_email", "crm_export_all"]
    assert gateway.startup_report(names, registry) == ["stripe_create_payment", "crm_export_all"]
    assert gateway.startup_report(names, {}) == names


def _fake_root(tmp_path, files=("stripe_server.py", "email_server.py", "crm_server.py")):
    for f in files:
        (tmp_path / f).write_text("")
    return tmp_path


ENV = {"STRIPE_API_KEY": "rk_test_x", "RESEND_API_KEY": "re_x", "SMTP_HOST": "h",
       "SMTP_PASS": "p", "EMAIL_FROM": "a@b.c", "SMTP_USER": "", "ANTHROPIC_API_KEY": "sk"}


def test_build_upstreams_all_present(tmp_path):
    root = _fake_root(tmp_path)
    servers = gateway.build_upstreams(ENV, root)["mcpServers"]
    assert set(servers) == {"stripe", "email", "crm"}
    for name, s in servers.items():
        assert s["command"] == gateway.sys.executable
        assert s["args"] == [str(root / f"{name}_server.py")]
    assert servers["stripe"]["env"] == {"STRIPE_API_KEY": "rk_test_x"}
    assert servers["email"]["env"] == {"RESEND_API_KEY": "re_x", "SMTP_HOST": "h",
                                       "SMTP_PASS": "p", "EMAIL_FROM": "a@b.c"}
    assert "STRIPE_API_KEY" not in servers["email"]["env"]
    assert not servers["crm"].get("env")


def test_build_upstreams_skips_missing_server(tmp_path, capsys):
    root = _fake_root(tmp_path, files=("email_server.py", "crm_server.py"))
    servers = gateway.build_upstreams(ENV, root)["mcpServers"]
    assert set(servers) == {"email", "crm"}
    assert "WARNING" in capsys.readouterr().out


def test_startup_check_works_when_token_set(monkeypatch):
    # The listing must not go through the token-checking Guard: an in-process
    # client sends no Authorization header.
    monkeypatch.setenv("GUARD_TOKEN", "s3cret")
    registry = {"email_send_email": {"category": "email"}}
    missing = run(gateway.startup_check(mock_server, registry))
    assert missing and "email_send_email" not in missing
    assert "email_send_email" in run(gateway.startup_check(mock_server, {}))
