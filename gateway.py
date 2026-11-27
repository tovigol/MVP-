"""AgentGuard gateway: FastMCP proxy on /mcp (skeleton: no policy/logging yet).

FastMCP facts verified against installed fastmcp 4.2.6 source:
 (a) create_proxy accepts a FastMCP instance (also Client, transport, MCPConfig dict).
     build_gateway therefore takes either a config dict or a FastMCP instance.
 (b) context.message.name in on_call_tool is the prefixed name (email_send_email).
 (c) A ToolError raised in middleware reaches the client as isError: true
     (client raises ToolError on call_tool).
 (d) get_http_headers() strips `authorization` by default; include_all=True is required.
 (e) see task-1-report.md (spawn behaviour and measured latency).
 NOTE: create_proxy on a single-server MCPConfig does NOT prefix tool names, so
     build_gateway mounts each upstream with namespace=<server name>.
"""
import asyncio
import hmac
import os
import sys
from pathlib import Path

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from shared import store
from shared.policy import Decision, evaluate, load_policy, load_registry, log_fields

UPSTREAMS = {
    "mcpServers": {
        "email": {
            "command": sys.executable,
            "args": [str(Path(__file__).resolve().parent / "dev" / "mock_email_server.py")],
        }
    }
}

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def check_bind(host: str, token: str | None) -> None:
    if host not in LOOPBACK and not token:
        raise SystemExit(
            f"Refusing to bind to non-loopback host {host!r} without GUARD_TOKEN set."
        )


class Guard(Middleware):
    def __init__(self, token: str | None = None):
        self.token = token

    async def on_request(self, context: MiddlewareContext, call_next):
        if self.token:
            header = get_http_headers(include_all=True).get("authorization", "")
            expected = f"Bearer {self.token}"
            if not hmac.compare_digest(header.encode(), expected.encode()):
                raise ToolError("Unauthorized")
        return await call_next(context)

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool = context.message.name
        args = context.message.arguments or {}
        agent = get_http_headers(include_all=True).get("x-agent") or "agent"
        # Fail closed. No await from here through log_event: one asyncio loop
        # stays atomic, so concurrent payments cannot both pass the daily limit.
        category = amount_usd = None
        try:
            registry = load_registry()
            category, amount_usd = log_fields(tool, args, registry)
            decision = evaluate(tool, args, load_policy(), registry,
                                store.daily_payment_total())
        except Exception as exc:
            decision = Decision("deny", None, f"Check failed: {exc}", "Policy check failed")
        try:
            event_id = store.log_event(tool, args, decision, category, amount_usd, agent)
        except Exception as exc:
            raise ToolError("Denied: guard could not record this call") from exc
        if decision.effect == "ask":
            # Still no await since log_event: create the approval before waiting.
            approval_id = store.create_approval(event_id)
            return await self._ask(context, call_next, event_id, approval_id)
        if decision.effect != "allow":
            reason = decision.reason_for_agent.rstrip(". ")
            raise ToolError(f"Blocked by policy: {reason}. Tell the user.")
        return await self._forward(context, call_next, event_id)

    async def _ask(self, context, call_next, event_id: int, approval_id: str):
        timeout = float(os.environ.get("GUARD_APPROVAL_TIMEOUT", 60))
        try:
            status = await store.wait_for_approval(approval_id, timeout)
        except asyncio.CancelledError:
            # The client gave up: close the approval so no stale buttons remain.
            store.decide_approval(approval_id, "rejected", "timeout")
            store.update_outcome(event_id, "timeout")
            raise
        if status == "timeout":
            # Close the approval first (needs outcome NULL), then record the outcome.
            if not store.decide_approval(approval_id, "rejected", "timeout"):
                status = store.approval_status(approval_id)  # owner decided at the last moment
        if status == "approved":
            store.update_outcome(event_id, "approved")
            return await self._forward(context, call_next, event_id)
        if status == "rejected":
            store.update_outcome(event_id, "rejected")
            raise ToolError("The owner rejected this action.")
        store.update_outcome(event_id, "timeout")
        raise ToolError(f"Pending owner approval (id {approval_id}). Tell the user and stop.")

    async def _forward(self, context, call_next, event_id: int):
        try:
            result = await call_next(context)
        except BaseException:
            store.update_outcome(event_id, "error")
            raise
        # A proxied upstream failure comes back as an error result, not a raise.
        if getattr(result, "is_error", False):
            store.update_outcome(event_id, "error")
        return result


def build_gateway(config, token: str | None = None) -> FastMCP:
    """config: an MCPConfig dict, or a FastMCP instance (mounted as `email`).

    Each upstream is mounted under its own namespace so tools are always
    prefixed (create_proxy alone does not prefix a single-server config).
    """
    gw = FastMCP("AgentGuard")
    if isinstance(config, FastMCP):
        gw.mount(create_proxy(config), namespace="email")
    else:
        for name, server in config["mcpServers"].items():
            gw.mount(create_proxy({"mcpServers": {name: server}}), namespace=name)
    gw.add_middleware(Guard(token))
    return gw


def main() -> None:
    host = os.environ.get("GUARD_HOST", "127.0.0.1")
    port = int(os.environ.get("GUARD_PORT", "8080"))
    token = os.environ.get("GUARD_TOKEN") or None
    check_bind(host, token)
    build_gateway(UPSTREAMS, token).run(transport="http", host=host, port=port, path="/mcp")


if __name__ == "__main__":
    main()
