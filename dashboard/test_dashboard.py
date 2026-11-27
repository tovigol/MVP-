"""Unit tests for dashboard/data.py (no Streamlit, no database)."""
from datetime import date, datetime

from dashboard import data


def ev(id, effect, outcome, ts="2026-11-27T14:03:00", **kw):
    return {"id": id, "ts": ts, "agent": "demo", "tool": kw.pop("tool", "t"), "category": "payment",
            "args_json": "{}", "amount_usd": None, "effect": effect, "rule_id": None,
            "reason": "", "outcome": outcome, **kw}


EVENTS = [
    ev(1, "allow", "forwarded"),
    ev(2, "allow", "forwarded"),
    ev(3, "deny", "blocked"),
    ev(4, "ask", "approved"),
    ev(5, "ask", "timeout"),
    ev(6, "ask", None),
    ev(7, "allow", "error"),
]


def test_status_of():
    assert [data.status_of(e) for e in EVENTS] == [
        "forwarded", "forwarded", "blocked", "approved", "timeout", "pending", "error"]


def test_counters():
    c = data.counters(EVENTS)
    assert c["total"] == 7
    assert c["automatic"] == 2 and c["automatic_pct"] == 29
    assert c["blocked"] == 1
    assert c["owner"] == 3
    assert (c["owner_approved"], c["owner_timeout"], c["owner_pending"]) == (1, 1, 1)
    assert c["errors"] == 1


def test_counters_empty():
    assert data.counters([])["automatic_pct"] == 0


def test_today_only():
    events = [ev(1, "allow", "forwarded", ts="2026-11-27T09:00:00"),
              ev(2, "allow", "forwarded", ts="2026-11-26T23:59:59")]
    assert [e["id"] for e in data.today_only(events, date(2026, 11, 27))] == [1]


def test_amount_prefers_column_then_facts():
    assert data.amount_of({"amount_usd": 80.0}) == 80.0
    assert data.amount_of({"amount_usd": None, "facts_json": '{"amount": 12.5}'}) == 12.5
    assert data.amount_of({"facts_json": "not json"}) is None
    assert data.amount_of({}) is None


def test_recipients_and_summary():
    row = {"facts_json": '{"recipients": ["gmail.com", "yossibakery.com"]}'}
    assert data.recipients_of(row) == ["gmail.com", "yossibakery.com"]
    assert data.summary_of(row) == "→ gmail.com, yossibakery.com"
    assert data.summary_of({"amount_usd": 80}) == "$80.00"
    # before facts_json exists: fall back to to/cc/bcc in the args
    args_row = {"args_json": '{"to": ["a@x.com"], "cc": "b@y.com", "bcc": ["a@x.com"]}'}
    assert data.recipients_of(args_row) == ["a@x.com", "b@y.com"]
    assert data.recipients_of({"args_json": "oops"}) == []


def test_destructive_reason():
    row = {"reason": "[DESTRUCTIVE] Deletes a customer"}
    assert data.is_destructive(row)
    assert data.reason_text(row) == "Can't be undone. Deletes a customer"
    assert not data.is_destructive({"reason": None})


def test_describe_in_plain_words():
    assert data.describe({"category": "payment", "amount_usd": 80}) == "Payment of $80.00"
    assert data.describe({"category": "payment"}) == "Payment"
    assert data.describe({"category": "email_send", "args_json": '{"to": ["a@x.com"]}'}) == "Email to a@x.com"
    three = {"category": "email_send", "facts_json": '{"recipients": ["a.com", "b.com", "c.com"]}'}
    assert data.describe(three) == "Email to a.com, b.com and 1 more"
    assert data.describe({"category": "read", "tool": "crm_get_customer"}) == "Looked up information (get customer)"
    assert data.describe({"category": "bulk_export", "tool": "crm_export_all"}) == "Export of all customer data"
    assert data.describe({"category": "unknown", "tool": "stripe_delete_customer"}) == "Delete customer"


def test_relative_time():
    now = datetime(2026, 11, 27, 14, 30, 0)
    assert data.relative_time("2026-11-27T14:29:30", now) == "just now"
    assert data.relative_time("2026-11-27T14:25:00", now) == "5 min ago"
    assert data.relative_time("2026-11-27T09:05:00", now) == "09:05"
    assert data.relative_time("2026-11-26T09:05:00", now) == "26 Nov 09:05"



def test_pretty_args_truncates_and_handles_raw_text():
    assert data.pretty_args('{"a": 1}') == '{\n  "a": 1\n}'
    assert data.pretty_args("not json") == "not json"
    assert data.pretty_args(None) == ""
    long = data.pretty_args('{"body": "' + "x" * 3000 + '"}', limit=100)
    assert long.endswith("(truncated)") and len(long) < 130


def test_seconds_left():
    now = datetime(2026, 11, 27, 14, 3, 20)
    assert data.seconds_left("2026-11-27T14:03:00", 60, now) == 40
    assert data.seconds_left("2026-11-27T14:00:00", 60, now) == 0
    assert data.seconds_left("garbage", 60, now) == 0


def test_to_frame_and_filter():
    df = data.to_frame(EVENTS)
    assert list(df["id"]) == [1, 2, 3, 4, 5, 6, 7]
    assert df.loc[2, "Result"] == "Stopped"
    assert df.loc[5, "Result"] == "Waiting for you"
    assert df.loc[0, "Rule"] == "default"
    assert list(data.filter_view(df, "Sent for approval")["id"]) == [4, 5, 6]
    assert list(data.filter_view(df, "Stopped")["id"]) == [3]
    assert len(data.filter_view(df, "Everything")) == 7
    assert len(data.filter_view(df, None)) == 7  # chip deselected


def test_result_cell_color_by_status():
    assert data.CRITICAL in data.result_cell_style("blocked")
    assert data.GOOD in data.result_cell_style("forwarded")
    assert data.tint("#ff0000", 0.5) == "rgba(255, 0, 0, 0.5)"


def test_new_blocked():
    assert [e["id"] for e in data.new_blocked(EVENTS, 0)] == [3]
    assert data.new_blocked(EVENTS, 3) == []


def test_hourly_counts():
    events = [ev(1, "allow", "forwarded", ts="2026-11-27T09:10:00"),
              ev(2, "deny", "blocked", ts="2026-11-27T09:50:00"),
              ev(3, "ask", None, ts="2026-11-27T14:00:00"),
              ev(4, "allow", "error", ts="2026-11-27T14:05:00")]  # errors aren't in any group
    hours = data.hourly_counts(events)
    assert len(hours) == 24
    assert hours[9] == {"hour": 9, "automatic": 1, "blocked": 1, "owner": 0}
    assert hours[14] == {"hour": 14, "automatic": 0, "blocked": 0, "owner": 1}


def test_category_breakdown():
    events = [{"category": "payment"}, {"category": "payment"}, {"category": "read"}, {"category": "weird"}]
    assert data.category_breakdown(events) == [("Payments", 2), ("Look-ups", 1), ("Other actions", 1)]


def test_greeting():
    assert data.greeting(datetime(2026, 1, 1, 8)) == "Good morning"
    assert data.greeting(datetime(2026, 1, 1, 13)) == "Good afternoon"
    assert data.greeting(datetime(2026, 1, 1, 2)) == "Good evening"


def test_charts_render_svg():
    from dashboard import charts
    hours = data.hourly_counts(EVENTS)
    bars = charts.hourly_bars(hours, 14)
    assert bars.startswith("<svg") and bars.endswith("</svg>") and "<title>14:00-14:59" in bars
    assert charts.hourly_bars(data.hourly_counts([])).count("<rect") == 24  # hit targets only
    ring = charts.donut([("Done", 3, data.GOOD), ("Stopped", 1, data.CRITICAL)], "4", "actions today")
    assert "Done: 3 (75%)" in ring and "Stopped: 1 (25%)" in ring
    assert "<title>" not in charts.donut([("Done", 0, data.GOOD)], "0", "actions today")
    assert "<polyline" in charts.sparkline([0, 2, 1], data.GOOD) and "<polyline" in charts.sparkline([], data.GOOD)
    assert charts._nice_ceiling(0) == 4 and charts._nice_ceiling(5) == 8 and charts._nice_ceiling(13) == 20 and charts._nice_ceiling(51) == 80


def test_default_database_is_the_real_guard_db():
    """Without GUARD_DB (unset or empty), every process must use the real guard.db, never a demo file."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    code = "from shared import store; print(store.DB_PATH.resolve())"
    for value in (None, "", "   "):
        env = {k: v for k, v in os.environ.items() if k != "GUARD_DB"}
        if value is not None:
            env["GUARD_DB"] = value
        out = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env,
                             capture_output=True, text=True, check=True).stdout.strip()
        assert Path(out) == (repo / "guard.db").resolve(), f"GUARD_DB={value!r} -> {out}"


def test_owner_summary_in_words():
    assert data.owner_summary(data.counters([])) == "Nothing needed your OK"
    assert data.owner_summary(data.counters([ev(1, "ask", "rejected")])) == "1 declined · All answered"
    assert data.owner_summary(data.counters(EVENTS)) == "1 approved · 1 expired · 1 waiting now"


def test_result_md_uses_material_icons():
    assert data.result_md("blocked") == ":material/block: Stopped"
    assert data.result_md("forwarded") == ":material/check_circle: Done automatically"
    assert data.result_md("something_new") == "something_new"
