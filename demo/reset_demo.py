"""Reset everything for the Noa Realty demo. Run from the repo root:

    python demo/reset_demo.py                     -> before the demo: the rules come from the questions screen
    python demo/reset_demo.py --fallback-policy   -> also install demo/policy_noa.json (if onboarding fails)

- Copies demo/customers_noa.csv -> customers.csv (the CRM server reads it on every call).
- Clears the events and approvals tables in guard.db, so the dashboard starts empty and
  today's payment total is $0 (rehearsals would otherwise use up the daily limit).
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shared import store  # noqa: E402

shutil.copyfile(ROOT / "demo" / "customers_noa.csv", ROOT / "customers.csv")

store.recent_events(1)  # creates the tables if guard.db is new
with sqlite3.connect(store.DB_PATH, timeout=10) as conn:
    conn.execute("DELETE FROM approvals")
    conn.execute("DELETE FROM events")

msg = "Demo reset: CRM = Noa Realty clients, guard.db cleared."
if "--fallback-policy" in sys.argv:
    from onboarding.policy_schema import validate_policy
    policy = json.loads((ROOT / "demo" / "policy_noa.json").read_text(encoding="utf-8"))
    problems = validate_policy(policy)
    if problems:
        raise SystemExit("demo policy is invalid: " + "; ".join(problems))
    shutil.copyfile(ROOT / "demo" / "policy_noa.json", ROOT / "shared" / "policy.json")
    msg += " Fallback policy installed."
else:
    msg += " Rules: set them on the questions screen (http://localhost:8000)."
print(msg)
