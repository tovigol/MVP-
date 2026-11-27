"""The Azure OpenAI demo agent (agent.py) with the Noa Realty story, without editing agent.py.

Run from the repo root:
    python demo/run_agent.py            -> runs demo/prompts_noa.txt, Enter before each prompt
    python demo/run_agent.py "prompt"   -> one prompt
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import agent  # noqa: E402

agent.SYSTEM_PROMPT = (ROOT / "demo" / "agent_prompt.txt").read_text(encoding="utf-8").replace(
    "Use only the agentguard tools: ", "Use the tools: ")

if len(sys.argv) == 1:
    sys.argv += ["--demo", "--file", "demo/prompts_noa.txt"]

asyncio.run(agent.main())
