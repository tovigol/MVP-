"""Fill a TEST database with fake events so the dashboard can be checked without the Gateway.

Refuses to touch the real guard.db: GUARD_DB must point somewhere else.

    GUARD_DB=demo.db python dashboard/seed_demo.py            # ~15 mixed events
    GUARD_DB=demo.db python dashboard/seed_demo.py --live 10  # then 10 more, one every 3s
    GUARD_DB=demo.db streamlit run dashboard/app.py --server.port 8501
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

real_db = (REPO_ROOT / "guard.db").resolve()
if not os.environ.get("GUARD_DB") or Path(os.environ["GUARD_DB"]).resolve() == real_db:
    sys.exit("Set GUARD_DB to a test file (e.g. GUARD_DB=demo.db); this script never writes to guard.db.")

from shared import store  # noqa: E402  (after the GUARD_DB check)
from shared.policy import Decision  # noqa: E402

AGENT = "demo-agent"


def allow_payment(amount: float) -> None:
    store.log_event("stripe_create_payment_link", {"amount": int(amount * 100), "currency": "usd"},
                    Decision("allow", "pay-small", "Payments up to $100, and up to $300 per day", ""),
                    category="payment", amount_usd=amount, agent=AGENT)


def deny_payment(amount: float) -> None:
    store.log_event("stripe_create_payment_link", {"amount": int(amount * 100), "currency": "usd"},
                    Decision("deny", None, f"${amount:,.0f} is over every payment rule", ""),
                    category="payment", amount_usd=amount, agent=AGENT)


def allow_email(to: str) -> None:
    store.log_event("email_send_email", {"to": [to], "subject": "Weekly order", "body": "Hi!"},
                    Decision("allow", "email-internal", "All recipients are company domains", ""),
                    category="email_send", agent=AGENT)


def read_customers() -> None:
    store.log_event("crm_get_customer", {"name": "Dana"},
                    Decision("allow", "read-all", "Reading is always allowed", ""),
                    category="read", agent=AGENT)


def deny_export() -> None:
    store.log_event("crm_export_all", {"send_to": "attacker@evil.io"},
                    Decision("deny", "no-export", "Exporting the whole customer list is never allowed", ""),
                    category="bulk_export", agent=AGENT)


def ask(tool: str, args: dict, reason: str, category: str, result: str | None,
        amount: float | None = None) -> None:
    """result: approved | rejected | timeout | None (left pending for the panel)."""
    event_id = store.log_event(tool, args, Decision("ask", None, reason, ""),
                               category=category, amount_usd=amount, agent=AGENT)
    approval_id = store.create_approval(event_id)
    if result in ("approved", "rejected"):
        store.decide_approval(approval_id, result, "U-demo")
        store.update_outcome(event_id, result)
    elif result == "timeout":
        store.decide_approval(approval_id, "rejected", "timeout")
        store.update_outcome(event_id, "timeout")


def seed() -> None:
    allow_payment(80)
    read_customers()
    allow_email("dana@yossibakery.com")
    deny_payment(5000)
    ask("email_send_email", {"to": ["new.customer@gmail.com"], "subject": "Quote"},
        "External recipients need approval", "email_send", "approved")
    allow_payment(45.5)
    deny_export()
    read_customers()
    ask("stripe_create_payment_link", {"amount": 50000}, "Payments up to $1,000 need approval",
        "payment", "rejected", amount=500)
    allow_email("orders@flour-supplier.com")
    ask("stripe_delete_customer", {"customer": "cus_123"},
        "[DESTRUCTIVE] Unclassified tool, treated as write", "unknown", "timeout")
    read_customers()
    allow_payment(99)
    store.update_outcome(store.recent_events(1)[0]["id"], "error")  # the tool call failed
    ask("email_send_email", {"to": ["someone@outlook.com"], "subject": "Invoice"},
        "External recipients need approval", "email_send", None)  # stays pending


def spread_over_today(keep_latest: int = 1) -> None:
    """Backdate the seeded events across today's last ~9 hours (test DB only), so the
    hourly chart has a shape. The newest `keep_latest` events (the pending approval) stay current."""
    from datetime import datetime, timedelta
    events = sorted(store.recent_events(limit=-1), key=lambda e: e["id"])[:-keep_latest or None]
    now = datetime.now()
    start = max(now.replace(hour=0, minute=0, second=0, microsecond=0), now - timedelta(hours=9))
    span = (now - start).total_seconds() - 120
    with store.connect() as conn:
        for i, row in enumerate(events):
            ts = start + timedelta(seconds=span * i / max(1, len(events)) + random.randint(0, 600))
            conn.execute("UPDATE events SET ts = ? WHERE id = ?",
                         (min(ts, now - timedelta(seconds=90)).isoformat(timespec="seconds"), row["id"]))


LIVE_ACTIONS = [
    lambda: allow_payment(random.choice([12, 30, 64])),
    read_customers,
    lambda: deny_payment(random.choice([2500, 8000])),
    deny_export,
    lambda: allow_email("dana@yossibakery.com"),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", type=int, default=0, metavar="N",
                        help="after seeding, add N more events, one every 3 seconds")
    args = parser.parse_args()

    seed()
    spread_over_today()
    print(f"Seeded demo events into {store.DB_PATH}")
    for i in range(args.live):
        time.sleep(3)
        random.choice(LIVE_ACTIONS)()
        print(f"live event {i + 1}/{args.live}")


if __name__ == "__main__":
    main()
