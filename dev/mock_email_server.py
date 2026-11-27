"""Mock upstream email MCP server. Prints each email and records it in CALLS."""
import sys

from fastmcp import FastMCP

mcp = FastMCP("mock-email")
CALLS: list[dict] = []


@mcp.tool
def send_email(
    to: str | list[str],
    cc: str | list[str] | None = None,
    bcc: str | list[str] | None = None,
    subject: str = "",
    body: str = "",
) -> str:
    """Send an email."""
    CALLS.append({"to": to, "cc": cc, "bcc": bcc, "subject": subject, "body": body})
    print(f"[mock-email] to={to} cc={cc} bcc={bcc} subject={subject!r} body={body!r}", file=sys.stderr, flush=True)
    n = sum(1 if isinstance(x, str) else len(x) for x in (to, cc, bcc) if x)
    return f"sent to {n} recipients"


if __name__ == "__main__":
    mcp.run()
