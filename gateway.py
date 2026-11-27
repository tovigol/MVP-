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
        if decision.effect != "allow":
            if decision.effect == "ask":
                # Task 4 replaces this with the approval flow.
                store.update_outcome(event_id, "blocked")
            reason = decision.reason_for_agent.rstrip(". ")
            raise ToolError(f"Blocked by policy: {reason}. Tell the user.")
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
