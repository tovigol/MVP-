# agent.py (Azure OpenAI + FastMCP Client)
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from dotenv import dotenv_values
from fastmcp import Client
from openai import AsyncAzureOpenAI

BASE_DIR = Path(__file__).resolve().parent
SENSITIVE_PREFIXES = ("STRIPE_", "RESEND_", "SMTP_", "EMAIL_", "SLACK_", "ANTHROPIC_")
DEFAULT_GATEWAY_URL = "http://localhost:8080/mcp"
MCP_TIMEOUT_SEC = 120  # מרווח ביטחון מעל 60 שניות המתנה לאישור בסלאק
MAX_TURNS = 8

SYSTEM_PROMPT = (
    "You are the operations assistant of Yossi's Bakery. "
    "Always fulfill the user's requests by calling the available tools. "
    "Do not refuse actions on your own — the Guard policy layer enforces all business rules. "
    "When a tool returns a policy block, error, or pending approval message, "
    "clearly explain the reason to the user in Hebrew."
)


def isolate_agent_env() -> dict:
    """מנקה מפתחות טולים וטוען רק את הגדרות Azure OpenAI וכתובת ה-Gateway."""
    for key in list(os.environ.keys()):
        if key.startswith(SENSITIVE_PREFIXES):
            os.environ.pop(key, None)

    env_path = BASE_DIR / ".env"
    env_file = dotenv_values(env_path) if env_path.exists() else {}

    def get_var(name: str, default: str = "") -> str:
        return (os.getenv(name) or env_file.get(name) or default).strip()

    cfg = {
        "api_key": get_var("AZURE_OPENAI_API_KEY"),
        "endpoint": get_var("AZURE_OPENAI_ENDPOINT"),
        "deployment": get_var("AZURE_OPENAI_DEPLOYMENT"),
        "api_version": get_var("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        "gateway_url": get_var("GATEWAY_URL", DEFAULT_GATEWAY_URL),
    }

    missing = [k for k in ("api_key", "endpoint", "deployment") if not cfg[k]]
    if missing:
        print(
            f"שגיאה: חסרים משתני Azure ב-.env ({', '.join(missing)}). "
            "צריך AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_DEPLOYMENT.",
            file=sys.stderr,
        )
        sys.exit(1)

    return cfg


def _mcp_tools_to_openai(mcp_tools) -> list[dict]:
    converted = []
    for t in mcp_tools:
        schema = getattr(t, "inputSchema", None) or {"type": "object", "properties": {}}
        if "type" not in schema:
            schema = {**schema, "type": "object"}
        converted.append({
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": schema,
            },
        })
    return converted


def _format_tool_result(res) -> str:
    content = getattr(res, "content", None)
    if isinstance(content, list) and content:
        return "\n".join(getattr(block, "text", str(block)) for block in content)
    return str(res)


async def run_prompt(
    prompt: str,
    gateway_url: str | None = None,
    show_tool_calls: bool = True,
    azure_cfg: dict | None = None,
) -> str:
    cfg = azure_cfg or isolate_agent_env()
    url = gateway_url or cfg["gateway_url"]

    llm = AsyncAzureOpenAI(
        api_key=cfg["api_key"],
        azure_endpoint=cfg["endpoint"],
        api_version=cfg["api_version"],
    )

    try:
        async with Client(url, timeout=MCP_TIMEOUT_SEC) as mcp_client:
            mcp_tools = await mcp_client.list_tools()
            openai_tools = _mcp_tools_to_openai(mcp_tools)

            messages: list[dict] = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]

            for _ in range(MAX_TURNS):
                response = await llm.chat.completions.create(
                    model=cfg["deployment"],
                    messages=messages,
                    tools=openai_tools if openai_tools else None,
                )
                msg = response.choices[0].message

                if not msg.tool_calls:
                    return msg.content or ""

                messages.append({
                    "role": "assistant",
                    "content": msg.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    raw_args = tc.function.arguments or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        parsed_args = {}

                    if show_tool_calls:
                        print(f"  -> [האג'נט קורא לטול] {tool_name} | פרמטרים: {json.dumps(parsed_args, ensure_ascii=False)}")

                    try:
                        result = await mcp_client.call_tool(tool_name, parsed_args)
                        tool_text = _format_tool_result(result)
                    except Exception as exc:
                        # חסימת מדיניות / דחייה בסלאק / טיימאאוט חוזרת למודל כהודעה ברורה
                        tool_text = f"TOOL_BLOCKED_OR_ERROR: {exc}"
                        if show_tool_calls:
                            print(f"  <- [השומר החזיר שגיאה/חסימה] {exc}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_text,
                    })

            return "האג'נט הגיע למגבלת הצעדים בלי תשובה סופית."

    except Exception as exc:
        err = str(exc)
        lower = err.lower()
        if any(k in lower for k in ("connect", "refused", "8080", "unreachable", "http", "client")):
            friendly = f"שגיאה: ה-Gateway לא זמין בכתובת {url}. ודאו שהשרת רץ על פורט 8080.\nפרטים: {err}"
        else:
            friendly = f"שגיאה בהרצת האג'נט מול Azure: {err}"
        print(f"\n{friendly}", file=sys.stderr)
        return friendly
    finally:
        await llm.close()


async def run_demo_file(prompts_file: Path, gateway_url: str, pause: bool, azure_cfg: dict) -> None:
    lines = [
        line.strip()
        for line in prompts_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    for idx, prompt in enumerate(lines, start=1):
        print(f"\n{'=' * 60}")
        print(f"[תרחיש {idx}/{len(lines)}] פרומפט: {prompt}")
        print("=" * 60)
        if pause:
            input("לחצי Enter להרצת התרחיש...")
        response = await run_prompt(prompt, gateway_url, show_tool_calls=True, azure_cfg=azure_cfg)
        print(f"\nתשובת האג'נט:\n{response}")


async def main() -> None:
    azure_cfg = isolate_agent_env()

    parser = argparse.ArgumentParser(description="אג'נט הדמו של המאפייה של יוסי (Azure OpenAI)")
    parser.add_argument("prompt", nargs="?", help="פרומפט בודד להרצה")
    parser.add_argument("--demo", action="store_true", help="הרצת כל התרחישים מ-demo_prompts.txt")
    parser.add_argument("--file", default="demo_prompts.txt", help="נתיב לקובץ פרומפטים")
    parser.add_argument("--no-pause", action="store_true", help="הרצה רצופה בלי להמתין ל-Enter")
    parser.add_argument("--url", default=azure_cfg["gateway_url"], help="כתובת ה-Gateway")
    args = parser.parse_args()

    if args.demo:
        await run_demo_file(BASE_DIR / args.file, args.url, pause=not args.no_pause, azure_cfg=azure_cfg)
        return

    if args.prompt:
        print(await run_prompt(args.prompt, args.url, show_tool_calls=True, azure_cfg=azure_cfg))
        return

    print("מצב אינטראקטיבי (הקלידי exit ליציאה):")
    while True:
        user_input = input("\nיוסי > ").strip()
        if not user_input or user_input.lower() in ("exit", "quit"):
            break
        print("\nאג'נט >", await run_prompt(user_input, args.url, show_tool_calls=True, azure_cfg=azure_cfg))


if __name__ == "__main__":
    asyncio.run(main())