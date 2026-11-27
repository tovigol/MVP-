"""The Azure OpenAI demo agent (agent.py) with the Noa Realty story, without editing agent.py.

Run from the repo root:
    python demo/run_agent.py                 -> opens the questions page, waits for "Approve and save",
                                                then runs demo/prompts_noa.txt automatically, 7 seconds between prompts
    python demo/run_agent.py --no-wait       -> skip the questions; use the rules already saved
    python demo/run_agent.py --delay 10      -> 10 seconds between prompts
    python demo/run_agent.py --pause         -> press Enter before each prompt instead
    python demo/run_agent.py --only 1,3      -> run only some of the prompts
    python demo/run_agent.py "prompt"        -> one prompt

The "#" line above each prompt in the file is printed as its title, including which Slack button to click.
The delay starts after a prompt finishes, so a Slack approval step waits for your click first.
"""
import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402

agent.SYSTEM_PROMPT = (ROOT / "demo" / "agent_prompt.txt").read_text(encoding="utf-8").replace(
    "Use only the agentguard tools: ", "Use the tools: ")


def load_scenarios(path: Path) -> list[tuple[str, str]]:
    """[(title from the '#' line above, prompt)]."""
    scenarios, title = [], ""
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("#"):
            title = line.lstrip("# ").strip()
        elif line:
            scenarios.append((title, line))
            title = ""
    return scenarios


async def countdown(seconds: float) -> None:
    for left in range(int(seconds), 0, -1):
        print(f"\r  next action in {left}s... ", end="", flush=True)
        await asyncio.sleep(1)
    print("\r" + " " * 30 + "\r", end="", flush=True)


POLICY_PATH = ROOT / "shared" / "policy.json"
WEB_APP_URL = "http://localhost:8000"


async def wait_for_onboarding() -> None:
    """Step 0: open the questions page and wait until 'Approve and save' writes shared/policy.json."""
    import json
    import webbrowser

    start = POLICY_PATH.stat().st_mtime if POLICY_PATH.exists() else 0
    print("=" * 70)
    print("  0. SET UP THE RULES: answer the questions in the browser")
    print(f"     {WEB_APP_URL}  ->  'Create my rules'  ->  'Approve and save'")
    print("  The agent starts by itself as soon as the rules are saved.")
    print("=" * 70)
    webbrowser.open(WEB_APP_URL)
    waited = 0
    while not (POLICY_PATH.exists() and POLICY_PATH.stat().st_mtime > start):
        print(f"\r  waiting for 'Approve and save'... {waited}s ", end="", flush=True)
        await asyncio.sleep(1)
        waited += 1
    await asyncio.sleep(0.5)  # let the atomic write settle
    rules = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["rules"]
    print(f"\r  Rules saved ({len(rules)} rules). The Guard enforces them from now on." + " " * 10)
    for rule in rules:
        print(f"    [{rule['effect']:5}] {rule.get('explain', rule['id'])}")


async def run_all(args, cfg: dict) -> None:
    if not args.no_wait:
        await wait_for_onboarding()
        await countdown(args.delay)
    scenarios = load_scenarios(ROOT / args.file)
    if args.only:
        wanted = {int(n) for n in args.only.split(",")}
        scenarios = [s for i, s in enumerate(scenarios, 1) if i in wanted]
    for idx, (title, prompt) in enumerate(scenarios, 1):
        print("\n" + "=" * 70)
        print(f"  {title}" if title else f"  Scenario {idx}")
        if "SLACK" in title:
            print("  >>> GO TO SLACK NOW (60 seconds to answer) <<<")
        print("=" * 70)
        print(f"נועה > {prompt}\n")
        if args.pause:
            input("Press Enter to run...")
        response = await agent.run_prompt(prompt, args.url, show_tool_calls=True, azure_cfg=cfg)
        print(f"\nהסוכן > {response}")
        if idx < len(scenarios) and not args.pause:
            await countdown(args.delay)
    print("\n" + "=" * 70 + "\n  Demo finished. See the dashboard: http://localhost:8000\n" + "=" * 70)


async def main() -> None:
    cfg = agent.isolate_agent_env()
    parser = argparse.ArgumentParser(description="Noa Realty demo agent (Azure OpenAI through AgentGuard)")
    parser.add_argument("prompt", nargs="?", help="a single prompt to run")
    parser.add_argument("--file", default="demo/prompts_noa.txt")
    parser.add_argument("--delay", type=float, default=7, help="seconds between prompts (default 7)")
    parser.add_argument("--pause", action="store_true", help="wait for Enter before each prompt")
    parser.add_argument("--only", help="comma-separated prompt numbers, e.g. 1,3")
    parser.add_argument("--no-wait", action="store_true",
                        help="don't wait for the questions screen; use the rules already saved")
    parser.add_argument("--url", default=cfg["gateway_url"])
    args = parser.parse_args()
    if args.prompt:
        print(await agent.run_prompt(args.prompt, args.url, show_tool_calls=True, azure_cfg=cfg))
    else:
        await run_all(args, cfg)


if __name__ == "__main__":
    asyncio.run(main())
