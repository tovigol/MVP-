"""SQLite storage for AgentGuard (guard.db).

Each function opens its own short-lived connection, so it is safe to call
from different threads. Pass this module as `db` to policy.evaluate().
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "guard.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT,
    agent TEXT,
    tool TEXT,
    category TEXT,
    args_json TEXT,
    amount_usd REAL,
    effect TEXT,
    rule_id TEXT,
    reason_he TEXT,
    outcome TEXT  -- forwarded | blocked | approved | rejected | timeout | error
);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    event_id INTEGER,
    status TEXT,  -- pending | approved | rejected
    decided_by TEXT,
    decided_at TEXT
);
"""

APPROVAL_TIMEOUT_SECONDS = 60


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def daily_total_usd() -> float:
    """Sum of today's payments with outcome forwarded or approved."""
    with connect() as conn:
        row = conn.execute(
            """SELECT COALESCE(SUM(amount_usd), 0) FROM events
               WHERE category = 'payment'
                 AND substr(ts, 1, 10) = ?
                 AND outcome IN ('forwarded', 'approved')""",
            (date.today().isoformat(),),
        ).fetchone()
    return float(row[0])


def log_event(agent: str, tool: str, category: str | None, args: dict, amount_usd: float | None,
              effect: str, rule_id: str | None, reason_he: str, outcome: str) -> int:
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO events
               (ts, agent, tool, category, args_json, amount_usd, effect, rule_id, reason_he, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), agent, tool, category, json.dumps(args, ensure_ascii=False),
             amount_usd, effect, rule_id, reason_he, outcome),
        )
        return cur.lastrowid


def create_approval(event_id: int) -> str:
    approval_id = uuid.uuid4().hex
    with connect() as conn:
        conn.execute(
            "INSERT INTO approvals (id, event_id, status) VALUES (?, ?, 'pending')",
            (approval_id, event_id),
        )
    return approval_id


def wait_for_approval(approval_id: str, timeout: float = APPROVAL_TIMEOUT_SECONDS,
                      poll_interval: float = 1.0) -> str:
    """Polls the approvals table. Returns 'approved', 'rejected' or 'timeout'."""
    deadline = time.monotonic() + timeout
    while True:
        with connect() as conn:
            row = conn.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is not None and row["status"] in ("approved", "rejected"):
            return row["status"]
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(poll_interval)


init_db()
