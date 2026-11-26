"""Live Slack test for the approval bot. Run from the repo root:  python slack_bot/live_test.py

Uses a temporary database, so the real guard.db is never touched.
Needs the same .env as the bot (SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_APPROVAL_CHANNEL).

Posts 3 approvals to the channel and plays the Gateway's part:
  1. Payment of $80 with a [DESTRUCTIVE] reason -> red header. Click Approve.
  2. Email to yossibakery.com, gmail.com          -> click Don't allow.
  3. Email "Leave me alone"                       -> don't click; after 60s it
     becomes "Expired" (rejected, decided_by="timeout") and the buttons go away.
The test stops by itself about 30 seconds after the expiry.

Not named test_*.py on purpose, so pytest doesn't pick it up and post to Slack.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_DB = Path(tempfile.gettempdir()) / "agentguard_slack_live_test.db"
TEST_DB.unlink(missing_ok=True)
os.environ["GUARD_DB"] = str(TEST_DB)  # must be set before shared.store is imported
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared import store  # noqa: E402

# Until the Gateway's facts_json change lands in store.py, add the column for this test only.
store.init_db()
with sqlite3.connect(store.DB_PATH) as conn:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(events)")}
    if "facts_json" not in columns:
        conn.execute("ALTER TABLE events ADD COLUMN facts_json TEXT")
if "facts_json" not in store._APPROVAL_SELECT:
    store._APPROVAL_SELECT = store._APPROVAL_SELECT.replace("e.args_json,", "e.args_json, e.facts_json,")

from slack_bolt.adapter.socket_mode import SocketModeHandler  # noqa: E402

from slack_bot import bot  # noqa: E402


class FakeDecision:
    def __init__(self, reason: str, rule_id: str | None):
        self.effect, self.rule_id, self.reason = "ask", rule_id, reason


def gateway_wait(event_id: int, approval_id: str) -> None:
    """What the Gateway does: wait 60s, then expire the approval if nobody answered."""
    deadline = time.monotonic() + store.APPROVAL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = store.approval_status(approval_id)
        if status in ("approved", "rejected"):
            store.update_outcome(event_id, status)
            print(f"gateway: {approval_id} -> {status}", flush=True)
            return
        time.sleep(1)
    with store.connect() as conn:
        conn.execute(
            "UPDATE approvals SET status = 'rejected', decided_by = 'timeout', decided_at = ? "
            "WHERE id = ? AND status = 'pending'",
            (store._now(), approval_id),
        )
    store.update_outcome(event_id, "timeout")
    print(f"gateway: {approval_id} -> expired (timeout)", flush=True)


def make_approval(tool: str, category: str, args: dict, facts: dict, reason: str, rule_id: str) -> None:
    event_id = store.log_event(tool, args, FakeDecision(reason, rule_id), category=category, agent="demo-agent")
    with store.connect() as conn:
        conn.execute("UPDATE events SET facts_json = ? WHERE id = ?", (json.dumps(facts), event_id))
    approval_id = store.create_approval(event_id)
    threading.Thread(target=gateway_wait, args=(event_id, approval_id), daemon=True).start()
    print(f"created approval {approval_id} ({tool})", flush=True)


def main() -> None:
    app = bot.create_app()
    threading.Thread(target=bot.poll_loop, args=(app.client,), daemon=True, name="poll").start()
    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])
    handler.connect()
    print(f"bot connected, posting to {bot.CHANNEL}", flush=True)

    make_approval("stripe_create_payment", "payment",
                  {"amount": 8000, "currency": "usd", "customer": "cus_123"},
                  {"amount": 80.0}, "[DESTRUCTIVE] Payments over $50 need approval", "pay_over_50")
    make_approval("gmail_send", "email_send",
                  {"to": ["yossi@yossibakery.com", "friend@gmail.com"], "subject": "Order"},
                  {"recipients": ["yossibakery.com", "gmail.com"]},
                  "Emails to outside domains need approval", "ext_email")
    make_approval("gmail_send", "email_send",
                  {"to": ["x@gmail.com"], "subject": "Leave me alone"},
                  {"recipients": ["gmail.com"]},
                  "Emails to outside domains need approval", "ext_email")

    try:
        time.sleep(store.APPROVAL_TIMEOUT_SECONDS + 30)  # click, watch the expiry, try a late click
    except KeyboardInterrupt:
        pass
    print("test finished", flush=True)
    handler.close()


if __name__ == "__main__":
    main()
