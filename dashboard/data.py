"""Pure helpers for the dashboard: event rows -> plain-language table, counters, colors.

No Streamlit and no database access here, so everything is unit-testable
(see test_dashboard.py). app.py fetches rows via shared.store and passes them in.

The audience is a small-business owner, not an engineer: every label here is
written in plain words; tool names and rule ids are only "technical details".
"""
from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

DESTRUCTIVE_TAG = "[DESTRUCTIVE]"
ARGS_PREVIEW_CHARS = 1200

# Status palette (dataviz reference: good / warning / critical). Always paired with
# words (and an icon where one can be drawn), so color never carries the meaning alone.
GOOD, WARNING, CRITICAL, NEUTRAL, ACCENT = "#0aac23", "#fbb315", "#dc3c3d", "#8a8a8a", "#2f6fdf"

# One status per event -> Material Symbols icon name, plain words, and its color family.
# Icon names: https://fonts.google.com/icons (Streamlit bundles the Rounded style).
STATUSES = {
    "forwarded": ("check_circle", "Done automatically", GOOD),
    "blocked": ("block", "Stopped", CRITICAL),
    "pending": ("hourglass_top", "Waiting for you", WARNING),
    "approved": ("thumb_up", "Approved", ACCENT),
    "rejected": ("front_hand", "Declined", NEUTRAL),
    "timeout": ("timer_off", "Expired, nothing was done", NEUTRAL),
    "error": ("error", "Failed", CRITICAL),
}

UNDOABLE_PREFIX = "Can't be undone. "

# Filter chips -> which statuses they show.
VIEWS = {
    "Everything": None,
    "Done automatically": {"forwarded"},
    "Stopped": {"blocked"},
    "Sent for approval": {"pending", "approved", "rejected", "timeout"},
}

TOOL_PREFIXES = ("stripe_", "email_", "gmail_", "crm_")

COLUMNS = ["id", "When", "What happened", "Result", "Why", "Tool", "Agent", "Rule", "Category",
           "status", "ts"]
PLAIN_COLUMNS = ["When", "What happened", "Result", "Why"]
TECH_COLUMNS = ["Tool", "Agent", "Rule", "Category"]


# --- single-row helpers --------------------------------------------------------

def load_json(text) -> dict | list | None:
    """json.loads that never raises (old rows, NULLs, non-JSON args)."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def facts_of(row: dict) -> dict:
    facts = load_json(row.get("facts_json"))
    return facts if isinstance(facts, dict) else {}


def amount_of(row: dict) -> float | None:
    """Dollars: amount_usd column, else facts_json["amount"] (new Gateway contract)."""
    for value in (row.get("amount_usd"), facts_of(row).get("amount")):
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def recipients_of(row: dict) -> list[str]:
    """facts_json["recipients"] (domains); before that lands, raw to/cc/bcc from the args."""
    recipients = facts_of(row).get("recipients")
    if isinstance(recipients, list):
        return [str(r) for r in recipients]
    args = load_json(row.get("args_json"))
    found: list[str] = []
    if isinstance(args, dict):
        for field in ("to", "cc", "bcc"):
            value = args.get(field)
            for item in (value if isinstance(value, list) else [value] if value else []):
                if str(item) not in found:
                    found.append(str(item))
    return found


def status_of(row: dict) -> str:
    """One status per row. outcome NULL on an ask = still waiting for the owner."""
    outcome = row.get("outcome")
    if outcome in STATUSES:
        return outcome
    if row.get("effect") == "ask":
        return "pending"
    return "error" if outcome else "pending"


def result_text(status: str) -> str:
    """Plain words, for the activity table (a dataframe cell can't draw icons; the cell color helps)."""
    return STATUSES.get(status, ("", status, NEUTRAL))[1]


def result_md(status: str) -> str:
    """Icon + words, for Streamlit markdown (':material/name:' renders as a Material Symbol)."""
    icon = STATUSES.get(status, ("",))[0]
    return f":material/{icon}: {result_text(status)}" if icon else result_text(status)


def is_destructive(row: dict) -> bool:
    return str(row.get("reason") or "").startswith(DESTRUCTIVE_TAG)


def reason_text(row: dict) -> str:
    """Reason in plain words: the [DESTRUCTIVE] tag becomes a short warning."""
    reason = str(row.get("reason") or "")
    if reason.startswith(DESTRUCTIVE_TAG):
        return UNDOABLE_PREFIX + reason[len(DESTRUCTIVE_TAG):].strip()
    return reason


def humanize_tool(tool) -> str:
    """'stripe_delete_customer' -> 'Delete customer'."""
    name = str(tool or "unknown action")
    for prefix in TOOL_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    name = name.replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def format_usd(amount: float | None) -> str:
    return "" if amount is None else f"${amount:,.2f}"


def describe(row: dict) -> str:
    """What the assistant tried to do, in the owner's words."""
    category = row.get("category") or ""
    if category == "payment":
        amount = amount_of(row)
        return f"Payment of {format_usd(amount)}" if amount is not None else "Payment"
    if category == "email_send":
        recipients = recipients_of(row)
        if not recipients:
            return "Send an email"
        more = f" and {len(recipients) - 2} more" if len(recipients) > 2 else ""
        return "Email to " + ", ".join(recipients[:2]) + more
    if category == "read":
        return f"Looked up information ({humanize_tool(row.get('tool')).lower()})"
    if category == "bulk_export":
        return "Export of all customer data"
    return humanize_tool(row.get("tool"))


def parse_ts(ts) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except (TypeError, ValueError):
        return None


def time_of(ts) -> str:
    parsed = parse_ts(ts)
    return parsed.strftime("%H:%M:%S") if parsed else str(ts or "")


def relative_time(ts, now: datetime | None = None) -> str:
    """'just now' / '5 min ago' / '14:03' (older than an hour) / '26 Nov 14:03' (other days)."""
    parsed = parse_ts(ts)
    if parsed is None:
        return str(ts or "")
    now = now or datetime.now()
    seconds = (now - parsed).total_seconds()
    if parsed.date() != now.date():
        return parsed.strftime("%d %b %H:%M")
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    return parsed.strftime("%H:%M")


def pretty_args(args_json, limit: int = ARGS_PREVIEW_CHARS) -> str:
    """Indented JSON when possible, raw text otherwise; truncated to `limit` chars."""
    parsed = load_json(args_json)
    text = json.dumps(parsed, indent=2, ensure_ascii=False) if parsed is not None else str(args_json or "")
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


def summary_of(row: dict) -> str:
    """Short 'what' for compact places: amount and/or recipients."""
    parts = []
    amount = amount_of(row)
    if amount is not None:
        parts.append(format_usd(amount))
    recipients = recipients_of(row)
    if recipients:
        parts.append("→ " + ", ".join(recipients))
    return " ".join(parts)


def seconds_left(ts, timeout: float, now: datetime | None = None) -> int:
    """Seconds until the Gateway gives up on an approval (never negative)."""
    parsed = parse_ts(ts)
    if parsed is None:
        return 0
    elapsed = ((now or datetime.now()) - parsed).total_seconds()
    return max(0, int(timeout - elapsed))


# --- table ---------------------------------------------------------------------

def to_frame(events: list[dict], now: datetime | None = None) -> pd.DataFrame:
    """Rows from store.recent_events() -> display DataFrame (newest first)."""
    records = []
    for row in events:
        status = status_of(row)
        records.append({
            "id": row.get("id"),
            "When": relative_time(row.get("ts"), now),
            "What happened": describe(row),
            "Result": result_text(status),
            "Why": reason_text(row),
            "Tool": row.get("tool") or "",
            "Agent": row.get("agent") or "",
            "Rule": row.get("rule_id") or "default",
            "Category": row.get("category") or "",
            "status": status,
            "ts": str(row.get("ts") or ""),
        })
    return pd.DataFrame(records, columns=COLUMNS)


def filter_view(df: pd.DataFrame, view: str | None) -> pd.DataFrame:
    statuses = VIEWS.get(view or "Everything")
    return df if statuses is None else df[df["status"].isin(statuses)]


def tint(hex_color: str, alpha: float) -> str:
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return f"rgba({r}, {g}, {b}, {alpha})"


def result_cell_style(status: str) -> str:
    """Soft tint + colored left edge on the Result cell only, so the table stays calm."""
    color = STATUSES.get(status, ("", "", NEUTRAL))[2]
    return f"background-color: {tint(color, 0.16)}; border-left: 4px solid {color}; font-weight: 600"


# --- counters ------------------------------------------------------------------

def today_only(events: list[dict], today: date | None = None) -> list[dict]:
    day = (today or date.today()).isoformat()
    return [e for e in events if str(e.get("ts") or "").startswith(day)]


def counters(events: list[dict]) -> dict:
    """README §2 success metric: automatic vs blocked vs reached the owner."""
    statuses = [status_of(e) for e in events]
    asks = [status_of(e) for e in events if e.get("effect") == "ask"]
    total = len(events)
    automatic = statuses.count("forwarded")
    return {
        "total": total,
        "automatic": automatic,
        "automatic_pct": round(100 * automatic / total) if total else 0,
        "blocked": statuses.count("blocked"),
        "owner": len(asks),
        "owner_approved": asks.count("approved"),
        "owner_rejected": asks.count("rejected"),
        "owner_timeout": asks.count("timeout"),
        "owner_pending": asks.count("pending"),
        "errors": statuses.count("error"),
    }




OUTCOME_GROUPS = {  # status -> the three groups the owner cares about
    "forwarded": "automatic", "blocked": "blocked",
    "pending": "owner", "approved": "owner", "rejected": "owner", "timeout": "owner",
}

CATEGORY_LABELS = {
    "payment": "Payments",
    "email_send": "Emails",
    "read": "Look-ups",
    "bulk_export": "Data exports",
}


def hourly_counts(events: list[dict]) -> list[dict]:
    """24 buckets (00-23) with automatic / blocked / owner counts, for bars and sparklines."""
    buckets = [{"hour": h, "automatic": 0, "blocked": 0, "owner": 0} for h in range(24)]
    for row in events:
        parsed = parse_ts(row.get("ts"))
        group = OUTCOME_GROUPS.get(status_of(row))
        if parsed and group:
            buckets[parsed.hour][group] += 1
    return buckets


def category_breakdown(events: list[dict]) -> list[tuple[str, int]]:
    """What the assistant worked on, in plain words, biggest first."""
    counts: dict[str, int] = {}
    for row in events:
        label = CATEGORY_LABELS.get(row.get("category") or "", "Other actions")
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    return "Good morning" if 5 <= hour < 12 else "Good afternoon" if 12 <= hour < 18 else "Good evening"


def owner_summary(c: dict) -> str:
    """The 'Approval requests today' card's second line, in words: what happened to those requests."""
    if not c["owner"]:
        return "Nothing needed your OK"
    parts = [f"{n} {word}" for n, word in ((c["owner_approved"], "approved"),
                                            (c["owner_rejected"], "declined"),
                                            (c["owner_timeout"], "expired")) if n]
    parts.append(f"{c['owner_pending']} waiting now" if c["owner_pending"] else "All answered")
    return " · ".join(parts)


def new_blocked(events: list[dict], last_seen_id: int) -> list[dict]:
    """Blocked events newer than last_seen_id, oldest first (for toasts)."""
    fresh = [e for e in events if (e.get("id") or 0) > last_seen_id and status_of(e) == "blocked"]
    return sorted(fresh, key=lambda e: e["id"])
