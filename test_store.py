"""Tests for the SQLite helpers, on a temporary guard.db. Run: python -m pytest -q"""
import asyncio
import sqlite3

import pytest

from shared import store
from shared.policy import Decision

ALLOW = Decision("allow", "pay-small", "ok", "ok")
ASK = Decision("ask", "pay-medium", "ok", "ok")
DENY = Decision("deny", "pay-large", "no", "no")


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "guard.db")


def test_wal_mode():
    store.recent_events()
    conn = sqlite3.connect(store.DB_PATH)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    conn.close()


def test_daily_total_counts_only_forwarded_and_approved_payments():
    store.log_event("pay", {}, ALLOW, "payment", 80.0)                    # forwarded
    store.log_event("pay", {}, DENY, "payment", 5000.0)                   # blocked
    store.update_outcome(store.log_event("pay", {}, ASK, "payment", 500.0), "approved")
    store.update_outcome(store.log_event("pay", {}, ASK, "payment", 700.0), "rejected")
    store.update_outcome(store.log_event("pay", {}, ALLOW, "payment", 30.0), "error")
    store.log_event("pay", {}, ASK, "payment", 900.0)                     # still pending
    store.log_event("email", {}, ALLOW, "email_send", None)
    assert store.daily_payment_total() == 580.0


def test_approval_round_trip():
    event_id = store.log_event("pay", {"amount": 50000}, ASK, "payment", 500.0)
    approval_id = store.create_approval(event_id)
    assert [p["approval_id"] for p in store.pending_approvals()] == [approval_id]
    assert store.decide_approval(approval_id, "approved", "tester")
    assert not store.decide_approval(approval_id, "rejected", "tester")   # already decided
    assert asyncio.run(store.wait_for_approval(approval_id, timeout=5)) == "approved"
    assert store.pending_approvals() == []


def test_wait_times_out():
    approval_id = store.create_approval(store.log_event("pay", {}, ASK, "payment", 500.0))
    assert asyncio.run(store.wait_for_approval(approval_id, timeout=0)) == "timeout"


def test_invalid_outcome_rejected():
    with pytest.raises(ValueError):
        store.update_outcome(1, "maybe")
