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

from dotenv import dotenv_values
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server import create_proxy
from fastmcp.server.dependencies import get_http_headers
from fastmcp.server.middleware import Middleware, MiddlewareContext
from shared import store
from shared.policy import Decision, evaluate, load_policy, load_registry, log_fields

ROOT = Path(__file__).resolve().parent

# Which .env keys each upstream may see (exact names or prefixes).
_ENV_RULES = {
    "stripe": (("STRIPE_API_KEY",), ()),
    "email": (("RESEND_API_KEY",), ("SMTP_", "EMAIL_")),
    "crm": ((), ()),
}


def build_upstreams(env_values: dict, root: Path = ROOT) -> dict:
    """Build the MCPConfig of real upstream servers; skip any whose file is missing.

    env_values comes from dotenv_values (never load_dotenv), so keys never enter
    the gateway's own os.environ. The mcp stdio transport starts each child with
    a small safe default env (PATH, SYSTEMROOT, USERPROFILE, TEMP, ...), not the
    parent's full env, plus the `env` given here. So the child can start on
    Windows and sees only its own keys.
    """
    servers: dict = {}
    for name, (exact, prefixes) in _ENV_RULES.items():
        path = root / f"{name}_server.py"
        if not path.is_file():
            print(f"WARNING: {name} upstream skipped: {path} does not exist", flush=True)
            continue
        env = {k: v for k, v in env_values.items()
               if v and (k in exact or k.startswith(prefixes))}
        servers[name] = {"command": sys.executable, "args": [str(path)], "env": env}
    return {"mcpServers": servers}


UPSTREAMS = build_upstreams(dotenv_values(ROOT / ".env"))


def startup_report(tool_names: list[str], registry: dict) -> list[str]:
    """Tool names that have no registry entry (order kept)."""
    return [t for t in tool_names if t not in registry]


async def startup_check(config, registry: dict) -> list[str]:
    """List the upstream tools and return those missing from the registry.

    Uses a tokenless gateway (no Guard token): an in-process client sends no
    Authorization header, so a token-checking gateway would reject the listing.
    """
    async with Client(build_gateway(config, None)) as c:
        return startup_report([t.name for t in await c.list_tools()], registry)


LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def check_bind(host: str, token: str | None) -> None:
    if host not in LOOPBACK and not token:
        raise SystemExit(
            f"Refusing to bind to non-loopback host {host!r} without GUARD_TOKEN set."
        )


def _best_effort(fn, *args) -> None:
    try:
        fn(*args)
    except Exception:
        pass


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
            try:
                approval_id = store.create_approval(event_id)
            except Exception as exc:
                _best_effort(store.update_outcome, event_id, "timeout")
                raise ToolError("Denied: guard could not record this call") from exc
            return await self._ask(context, call_next, event_id, approval_id)
        if decision.effect != "allow":
            reason = decision.reason_for_agent.rstrip(". ")
            raise ToolError(f"Blocked by policy: {reason}. Tell the user.")
        return await self._forward(context, call_next, event_id)

    async def _ask(self, context, call_next, event_id: int, approval_id: str):
        timeout = float(os.environ.get("GUARD_APPROVAL_TIMEOUT", 60))
        try:
            status = await store.wait_for_approval(approval_id, timeout)
            if status == "timeout":
                # Close the approval first (needs outcome NULL), then record the outcome.
                if not store.decide_approval(approval_id, "rejected", "timeout"):
                    status = store.approval_status(approval_id)  # decided at the last moment
        except BaseException:
            # Cancelled or a store error: never leave a pending approval behind.
            # Cleanup is best-effort and must not mask the original exception.
            _best_effort(store.decide_approval, approval_id, "rejected", "timeout")
            _best_effort(store.update_outcome, event_id, "timeout")
            raise
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
    try:
        for name in asyncio.run(startup_check(UPSTREAMS, load_registry())):
            print(f"WARNING: tool {name} is not in the registry (it will be denied)", flush=True)
    except Exception as exc:
        print(f"WARNING: registry check SKIPPED, no tools were checked: {exc}", flush=True)
    gw = build_gateway(UPSTREAMS, token)
    gw.run(transport="http", host=host, port=port, path="/mcp")


if __name__ == "__main__":
    main()
