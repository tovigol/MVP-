"""SQLite storage for AgentGuard (guard.db). Spec: README.md sections 5.4-5.5.

Three processes share this file (Gateway, dashboard, Slack bot), so:
  - WAL mode, so readers never block the writer;
  - every function opens and CLOSES its own short-lived connection;
  - tables are created on first use, so any process can start first.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(os.environ.get("GUARD_DB", Path(__file__).resolve().parent.parent / "guard.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts TEXT, agent TEXT, tool TEXT, category TEXT,
  args_json TEXT, amount_usd REAL, effect TEXT, rule_id TEXT, reason TEXT,
  outcome TEXT  -- forwarded | blocked | approved | rejected | timeout | error
);
CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY, event_id INTEGER, status TEXT,  -- pending | approved | rejected
  decided_by TEXT, decided_at TEXT
);
"""

OUTCOMES = ("forwarded", "blocked", "approved", "rejected", "timeout", "error")
APPROVAL_TIMEOUT_SECONDS = 60
POLL_INTERVAL_SECONDS = 1.0

_initialized: set[str] = set()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")  # local time, like the daily total


def init_db() -> None:
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    _initialized.add(str(DB_PATH))


@contextmanager
def connect():
    """A connection that commits on success, rolls back on error, and always closes."""
    if str(DB_PATH) not in _initialized:
        init_db()
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# --- cumulative state ---------------------------------------------------------

def daily_payment_total() -> float:
    """Sum of amount_usd for today's payment events whose outcome is forwarded or approved."""
    with connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount_usd), 0) FROM events
               WHERE category = 'payment' AND substr(ts, 1, 10) = ?
                 AND outcome IN ('forwarded', 'approved')""",
            (date.today().isoformat(),),
        ).fetchone()
    return float(row[0])


daily_total_usd = daily_payment_total  # old name


# --- events -------------------------------------------------------------------

def log_event(tool: str, args, decision, category: str | None = None,
              amount_usd: float | None = None, agent: str | None = None) -> int:
    """Logs a decision and returns the event id.

    Initial outcome: allow -> forwarded, deny -> blocked, ask -> NULL until
    update_outcome() records approved / rejected / timeout.
    An allowed payment counts toward the daily total right away, so parallel
    calls can't slip past the limit; update_outcome(error) removes it again.
    """
    outcome = {"allow": "forwarded", "deny": "blocked"}.get(decision.effect)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (ts, agent, tool, category, args_json, amount_usd, effect, rule_id, reason, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), agent, tool, category, json.dumps(args, ensure_ascii=False, default=str),
             amount_usd, decision.effect, decision.rule_id, decision.reason, outcome),
        )
        return cur.lastrowid


def update_outcome(event_id: int, outcome: str) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"invalid outcome: {outcome}")
    with connect() as conn:
        conn.execute("UPDATE events SET outcome = ? WHERE id = ?", (outcome, event_id))


update_event_outcome = update_outcome  # old name


def recent_events(limit: int = 100) -> list[dict]:
    """Latest events, newest first (for the dashboard)."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


# --- approvals ----------------------------------------------------------------

def create_approval(event_id: int) -> str:
    approval_id = uuid.uuid4().hex[:8]  # short, so a person can type it into approve.py
    with connect() as conn:
        conn.execute("INSERT INTO approvals (id, event_id, status) VALUES (?, ?, 'pending')",
                     (approval_id, event_id))
    return approval_id


_APPROVAL_SELECT = """
    SELECT a.id AS approval_id, a.event_id, a.status, a.decided_by, a.decided_at,
           e.ts, e.agent, e.tool, e.category, e.args_json, e.amount_usd,
           e.rule_id, e.reason, e.outcome
    FROM approvals a JOIN events e ON e.id = a.event_id
"""


def pending_approvals() -> list[dict]:
    """Approvals the Gateway is still waiting on (excludes ones that already timed out)."""
    with connect() as conn:
        rows = conn.execute(
            _APPROVAL_SELECT + " WHERE a.status = 'pending' AND e.outcome IS NULL ORDER BY e.id"
        ).fetchall()
    return [dict(r) for r in rows]


def get_approval(approval_id: str) -> dict | None:
    """One approval joined with its event, or None."""
    with connect() as conn:
        row = conn.execute(_APPROVAL_SELECT + " WHERE a.id = ?", (approval_id,)).fetchone()
    return dict(row) if row else None


def decide_approval(approval_id: str, status: str, decided_by: str) -> bool:
    """Called by the Slack bot on a button click.

    Succeeds only while the row is pending and the Gateway is still waiting
    (the event has no outcome yet, i.e. it has not timed out).
    """
    if status not in ("approved", "rejected"):
        raise ValueError(f"invalid status: {status}")
    with connect() as conn:
        cur = conn.execute(
            """UPDATE approvals SET status = ?, decided_by = ?, decided_at = ?
               WHERE id = ? AND status = 'pending'
                 AND event_id IN (SELECT id FROM events WHERE outcome IS NULL)""",
            (status, decided_by, _now(), approval_id),
        )
        return cur.rowcount == 1


def approval_status(approval_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
    return row["status"] if row else None


async def wait_for_approval(approval_id: str, timeout: float = APPROVAL_TIMEOUT_SECONDS) -> str:
    """Polls the row every second without blocking the event loop.

    Returns "approved", "rejected" or "timeout". The caller records the outcome.
    """
    deadline = time.monotonic() + timeout
    while True:
        status = approval_status(approval_id)
        if status in ("approved", "rejected"):
            return status
        if time.monotonic() >= deadline:
            return "timeout"
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
