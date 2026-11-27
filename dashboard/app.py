"""AgentGuard live dashboard (Person 4). Spec: README.md sections 2, 4, 8, 9.

Written for the business owner, not an engineer. Card layout, top to bottom:
  1. Greeting + status pills (is the guard on, is anything waiting)
  2. KPI cards: total, done automatically, stopped, approval requests (with hourly sparklines)
  3. Activity by hour (stacked bars)  |  How actions were handled (donut)
  4. Waiting for your answer  |  Stopped for your safety  |  What it worked on
  5. Everything your assistant did (filterable log)

Read-only: every read goes through shared.store (no raw SQL, no writes).
Data source: the real guard.db by default. GUARD_DB (tests only) points elsewhere,
and then the screen shows a "Test data" banner so it is never mistaken for real activity.

The second page of the web app (the onboarding questions come first). Run from the repo root:
    streamlit run app.py --server.port 8000
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Streamlit puts this file's folder on sys.path, not the repo root; add it for `shared`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from dashboard import charts, data
from shared import policy, store, theme

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:  # the Slack link is a nice-to-have
    pass

REFRESH_SECONDS = 2
COUNTER_WINDOW = 500  # events read for today's numbers (enough for a demo day)
RECENT_BLOCKS = 4
DEFAULT_ROWS = 100
BRAND = theme.BRAND
SLACK_CHANNEL = os.environ.get("SLACK_APPROVAL_CHANNEL", "").strip()
SLACK_URL = f"https://slack.com/app_redirect?channel={SLACK_CHANNEL}" if SLACK_CHANNEL else ""

# True when reading the real guard.db; anything else (GUARD_DB set) is test data.
USING_REAL_DB = store.DB_PATH.resolve() == store.REAL_DB_PATH.resolve()

st.set_page_config(page_title="AgentGuard", page_icon=":material/shield:", layout="wide")

st.markdown(f"""
<style>
{theme.BASE_CSS}
.block-container {{ padding-top: 2.2rem; max-width: 1320px; }}

/* Cards side by side share one height (like the reference layouts). */
[data-testid="stHorizontalBlock"]:has(.ag-card) {{ align-items: stretch; }}
[data-testid="stColumn"]:has(.ag-card) > [data-testid="stVerticalBlock"],
[data-testid="stColumn"]:has(.ag-card) [data-testid="stElementContainer"],
[data-testid="stColumn"]:has(.ag-card) .stMarkdown,
[data-testid="stColumn"]:has(.ag-card) .stMarkdown > div,
[data-testid="stColumn"]:has(.ag-card) [data-testid="stMarkdownContainer"] {{ height: 100%; }}
[data-testid="stColumn"] .ag-card {{ height: calc(100% - 16px); box-sizing: border-box; }}

.ag-legend {{ display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; color: #475467; }}
.ag-donut {{ display: flex; align-items: center; gap: 18px; flex-wrap: wrap; justify-content: center; }}
.ag-donut-legend {{ flex: 1; min-width: 160px; }}
.ag-donut-row {{ display: flex; justify-content: space-between; font-size: 14px; color: #344054; padding: 7px 0;
                 border-bottom: 1px dashed #eef0f4; }}
.ag-track {{ height: 6px; border-radius: 3px; margin-top: 8px; overflow: hidden; }}
.ag-track span {{ display: block; height: 100%; border-radius: 3px; }}
.ag-bar-row {{ display: flex; justify-content: space-between; font-size: 14px; color: #344054; margin-top: 12px; }}
</style>
""", unsafe_allow_html=True)


def load_policy_safe() -> dict:
    """A missing or broken policy.json must not break the dashboard."""
    try:
        return policy.load_policy()
    except Exception:
        return {}


esc = theme.esc
mi = theme.mi


# --- sidebar (changing these reruns the whole page) -----------------------------

with st.sidebar:
    st.markdown(theme.logo_html("Your AI assistant's safety guard"), unsafe_allow_html=True)
    # In the sidebar, not above the greeting, so the page starts with the header.
    if st.button("Back to the questions", icon=":material/edit_note:", key="back_to_questions", width="stretch"):
        st.session_state["step"] = "questions"  # the answers stay in raw_answers, so the form opens filled in
        st.switch_page("onboarding/app.py")
    st.subheader("Settings")
    paused = st.toggle("Pause live updates", value=False, help="Freeze the screen, e.g. while presenting")
    show_tech = st.toggle("Show technical details", value=False,
                          help="Adds tool name, agent, rule and category to the activity list")
    try:  # SQLite treats LIMIT -1 as "no limit"; runs on full reruns only, not every 2s
        total_events = len(store.recent_events(limit=-1))
    except Exception:
        total_events = 0
    # Start at 100 (or all, if fewer); keep a remembered value inside the range.
    # Until the owner picks a number, follow the data: newest 100 (or all, if fewer).
    # This matters on a fresh guard.db, which starts empty and fills up while the page is open.
    if st.session_state.get("row_limit_auto", True):
        st.session_state["row_limit"] = min(DEFAULT_ROWS, total_events)
    elif st.session_state.get("row_limit", 0) > total_events:
        st.session_state["row_limit"] = total_events
    row_limit = st.number_input(
        "Actions to show", min_value=0, max_value=total_events, step=1, key="row_limit",
        on_change=lambda: st.session_state.update(row_limit_auto=False),
        help=f"Newest first. Use the arrows or type a number from 0 to {total_events} (all actions so far).")
    st.divider()
    if USING_REAL_DB:
        st.caption("Data: your real activity (guard.db)")
    else:
        st.caption(f":material/science: Test database: {store.DB_PATH}")


# --- HTML blocks ---------------------------------------------------------------

def header_html(business: str, pending: int, blocked: int) -> str:
    who = f" · {esc(business)}" if business else ""
    pills = [f'<span class="ag-pill"><span class="ag-dot" style="background:{data.GOOD}"></span>'
             f'Guard is on</span>']
    if not USING_REAL_DB:
        pills.insert(0, '<span class="ag-pill" style="background:#eef4ff;border-color:#c7d7fe;color:#3538cd">'
                        f'{mi("science")} Test data</span>')
    if pending:
        pills.append(f'<span class="ag-pill" style="background:#fff8e6;border-color:#fde7a8">'
                     f'{mi("hourglass_top", "#b54708")} {pending} waiting for you</span>')
    if blocked:
        pills.append(f'<span class="ag-pill" style="background:#fdeeee;border-color:#f8cfcf">'
                     f'{mi("block", data.CRITICAL)} {blocked} stopped today</span>')
    live = (f'{mi("pause_circle", data.NEUTRAL)} Paused' if paused
            else f'{mi("sensors", data.GOOD)} Live · {datetime.now():%H:%M:%S}')
    pills.append(f'<span class="ag-pill">{live}</span>')
    return (f'<div class="ag-top"><div><div class="ag-hello">{data.greeting()} {mi("waving_hand", "#f5a524")}</div>'
            f'<div class="ag-hello-sub">Here\'s what your AI assistant did today{who}. '
            f'Every action is checked against your rules first.</div></div>'
            f'<div class="ag-pills">{"".join(pills)}</div></div>')


def kpi_html(icon: str, color: str, label: str, value: str, sub: str, spark: list[int]) -> str:
    return (f'<div class="ag-card"><div class="ag-kpi-head"><span class="ag-icon" '
            f'style="background:{data.tint(color, .14)}">{mi(icon, color)}</span>{esc(label)}</div>'
            f'<div class="ag-kpi-body"><div><div class="ag-kpi-value">{esc(value)}</div>'
            f'<div class="ag-kpi-sub">{sub}</div></div>{charts.sparkline(spark, color)}</div></div>')


def kpis_html(c: dict, hours: list[dict]) -> str:
    now_hour = datetime.now().hour
    upto = hours[: now_hour + 1]
    spark = {k: [h[k] for h in upto] for k in ("automatic", "blocked", "owner")}
    spark_total = [h["automatic"] + h["blocked"] + h["owner"] for h in upto]
    owner_sub = esc(data.owner_summary(c))
    cards = [
        kpi_html("smart_toy", BRAND, "Total actions", str(c["total"]), "Checked by the guard today", spark_total),
        kpi_html("check_circle", data.GOOD, "Done automatically", str(c["automatic"]),
                 f'<b style="color:#067a18">{c["automatic_pct"]}%</b> of all actions', spark["automatic"]),
        kpi_html("block", data.CRITICAL, "Stopped", str(c["blocked"]), "Blocked before anything happened",
                 spark["blocked"]),
        kpi_html("fact_check", data.WARNING, "Approval requests today", str(c["owner"]), owner_sub,
                 spark["owner"]),
    ]
    return f'<div class="ag-kpis">{"".join(cards)}</div>'


def legend_html() -> str:
    return '<div class="ag-legend">' + "".join(
        f'<span><span class="ag-dot" style="background:{color}"></span>{label}</span>'
        for _, label, color in charts.GROUPS) + "</div>"


def activity_card_html(hours: list[dict]) -> str:
    busiest = max(hours, key=lambda h: h["automatic"] + h["blocked"] + h["owner"])
    busy_total = busiest["automatic"] + busiest["blocked"] + busiest["owner"]
    sub = (f"Busiest hour: {busiest['hour']:02d}:00 with {busy_total} actions" if busy_total
           else "No actions yet today")
    return (f'<div class="ag-card"><div style="display:flex;justify-content:space-between;align-items:flex-start;'
            f'flex-wrap:wrap;gap:8px"><div><div class="ag-card-title">Activity by hour</div>'
            f'<div class="ag-card-sub">{sub}</div></div>{legend_html()}</div>'
            f'{charts.hourly_bars(hours, datetime.now().hour)}</div>')


def donut_card_html(c: dict) -> str:
    segments = [("Done automatically", c["automatic"], data.GOOD),
                ("Sent for approval", c["owner"], data.WARNING),
                ("Stopped", c["blocked"], data.CRITICAL)]
    rows = "".join(
        f'<div class="ag-donut-row"><span><span class="ag-dot" style="background:{color}"></span>{label}</span>'
        f'<b>{round(100 * n / c["total"]) if c["total"] else 0}%</b></div>' for label, n, color in segments)
    return (f'<div class="ag-card"><div class="ag-card-title">How actions were handled</div>'
            f'<div class="ag-card-sub">Today, by result</div><div class="ag-donut">'
            f'{charts.donut(segments, str(c["total"]), "actions today")}'
            f'<div class="ag-donut-legend">{rows}</div></div></div>')


def waiting_card_html(pending: list[dict]) -> str:
    items = []
    for row in pending:
        left = data.seconds_left(row.get("ts"), store.APPROVAL_TIMEOUT_SECONDS)
        pct = round(100 * left / store.APPROVAL_TIMEOUT_SECONDS)
        timer = f"{left}s left to answer" if left else "Time's up. Cancelling, nothing will happen."
        flag = '<span class="ag-badge" style="background:#fdeeee;color:#b42318">Can\'t be undone</span>' \
            if data.is_destructive(row) else ""
        why = data.reason_text(row).replace(data.UNDOABLE_PREFIX, "") or "Not covered by your rules"
        button = (f'<a class="ag-btn" href="{esc(SLACK_URL)}" target="_blank">Answer in Slack'
                  f'{mi("arrow_forward")}</a>') \
            if SLACK_URL else '<div class="ag-item-meta">Answer in Slack, #agent-approvals</div>'
        items.append(f'<div class="ag-item"><div class="ag-item-title">{esc(data.describe(row))}{flag}</div>'
                     f'<div class="ag-item-meta">{esc(why)}</div>'
                     f'<div class="ag-track" style="background:{data.tint(data.WARNING, .18)}">'
                     f'<span style="width:{pct}%;background:{data.WARNING}"></span></div>'
                     f'<div class="ag-item-meta">{mi("timer")} {timer}</div>{button}</div>')
    body = "".join(items) or (f'<div class="ag-empty">{mi("task_alt", data.GOOD)}Nothing is waiting for you. '
                              'Approvals show up here and in Slack.</div>')
    count = f" ({len(pending)})" if pending else ""
    return (f'<div class="ag-card"><div class="ag-card-title">{mi("hourglass_top", "#b54708")}'
            f'Waiting for your answer{count}</div>'
            f'<div class="ag-card-sub">The assistant pauses until you decide</div>{body}</div>')


def stopped_card_html(today_events: list[dict]) -> str:
    blocked = [e for e in today_events if data.status_of(e) == "blocked"][:RECENT_BLOCKS]
    items = "".join(
        f'<div class="ag-item"><div class="ag-item-title">{esc(data.describe(row))}'
        f'<span class="ag-badge" style="background:#fdeeee;color:#b42318">Stopped</span></div>'
        f'<div class="ag-item-meta">{esc(data.reason_text(row) or "Not allowed by your rules")} · '
        f'{esc(data.relative_time(row.get("ts")))}</div></div>' for row in blocked)
    body = items or f'<div class="ag-empty">{mi("verified_user", data.GOOD)}Nothing was stopped today.</div>'
    return (f'<div class="ag-card"><div class="ag-card-title">{mi("block", data.CRITICAL)}Stopped for your safety</div>'
            f'<div class="ag-card-sub">Blocked before they could happen</div>{body}</div>')


def worked_on_card_html(today_events: list[dict]) -> str:
    breakdown = data.category_breakdown(today_events)
    total = sum(n for _, n in breakdown) or 1
    rows = "".join(
        f'<div class="ag-bar-row"><span>{esc(label)}</span><b>{n}</b></div>'
        f'<div class="ag-track" style="background:{data.tint(BRAND, .14)}">'
        f'<span style="width:{round(100 * n / total)}%;background:{BRAND}"></span></div>'
        for label, n in breakdown)
    body = rows or '<div class="ag-empty">Nothing yet today.</div>'
    return (f'<div class="ag-card"><div class="ag-card-title">{mi("checklist", BRAND)}What your assistant worked on</div>'
            f'<div class="ag-card-sub">Today, by type of action</div>{body}</div>')


# --- widgets -------------------------------------------------------------------

def render_toasts(events: list[dict]) -> None:
    """Toast new blocked events. The first load only records where we are."""
    if not events:
        return
    newest = max(e["id"] for e in events)
    last_seen = st.session_state.get("last_seen_id")
    if last_seen is not None:
        for row in data.new_blocked(events, last_seen):
            st.toast(f"Stopped: {data.describe(row)}", icon=":material/block:")
    st.session_state["last_seen_id"] = max(newest, last_seen or 0)


def render_activity(events: list[dict]) -> None:
    with st.container(key="card_activity"):
        st.markdown('<div class="ag-card-title">Everything your assistant did</div>'
                    '<div class="ag-card-sub">Newest first. Filter by result.</div>', unsafe_allow_html=True)
        if not events:
            st.markdown('<div class="ag-empty">Nothing yet. As soon as your assistant does something, '
                        'it will show up here.</div>', unsafe_allow_html=True)
            return
        if not row_limit:
            st.caption("“Actions to show” is set to 0. Raise it in the sidebar to see the list.")
            return
        view = st.segmented_control("Show", list(data.VIEWS), default="Everything", key="view",
                                    label_visibility="collapsed")
        df = data.filter_view(data.to_frame(events[:row_limit]), view)
        if df.empty:
            st.caption("Nothing in this view yet.")
            return
        statuses = df["status"]
        styled = df.style.apply(lambda col: [data.result_cell_style(s) for s in statuses[col.index]],
                                subset=["Result"])
        columns = data.PLAIN_COLUMNS + (data.TECH_COLUMNS if show_tech else [])
        st.dataframe(styled, hide_index=True, width="stretch", column_order=columns,
                     column_config={"When": st.column_config.TextColumn(width="small"),
                                    "What happened": st.column_config.TextColumn(width="large"),
                                    "Result": st.column_config.TextColumn(width="medium"),
                                    "Why": st.column_config.TextColumn(width="large")})

        with st.expander("See the full details of one action"):
            by_id = {e["id"]: e for e in events}
            choice = st.selectbox("Action", df["id"].tolist(), index=None, placeholder="Pick an action",
                                  format_func=lambda i: f"{data.time_of(by_id[i].get('ts'))} · {data.describe(by_id[i])}",
                                  key="details_id")
            if choice is not None and choice in by_id:
                row = by_id[choice]
                st.markdown(f"**Result:** {data.result_md(data.status_of(row))}  \n"
                            f"**Why:** {data.reason_text(row) or '—'}  \n"
                            f"**Tool:** `{row.get('tool')}` · **Rule:** `{row.get('rule_id') or 'default'}`")
                st.code(data.pretty_args(row.get("args_json")), language="json")


# --- live part: only this fragment reruns every 2s, so the page doesn't flicker --

if st.session_state.get("row_limit_auto", True):
    row_limit = DEFAULT_ROWS  # sidebar max may be stale between full reruns; show the newest 100

# Outside the live fragment, so it isn't shown again every 2s.
if st.session_state.pop("just_saved", False):
    st.toast("Rules saved. The guard uses them from the next action.", icon=":material/task_alt:")


@st.fragment(run_every=None if paused else REFRESH_SECONDS)
def live() -> None:
    try:
        events = store.recent_events(limit=max(row_limit, COUNTER_WINDOW))
        pending = store.pending_approvals()
    except Exception as e:  # e.g. the DB is locked for a moment; try again next tick
        st.error(f"Couldn't load the latest activity, retrying… ({e})")
        return

    today_events = data.today_only(events)
    c = data.counters(today_events)
    hours = data.hourly_counts(today_events)

    if not USING_REAL_DB:
        st.warning(f"**Test data.** This screen is reading a test database ({store.DB_PATH.name}), "
                   "not your real activity. Start the dashboard without GUARD_DB to see guard.db.",
                   icon=":material/science:")
    st.markdown(header_html(load_policy_safe().get("business", ""), len(pending), c["blocked"]),
                unsafe_allow_html=True)
    st.markdown(kpis_html(c, hours), unsafe_allow_html=True)

    chart_col, donut_col = st.columns([2, 1], gap="medium")
    chart_col.markdown(activity_card_html(hours), unsafe_allow_html=True)
    donut_col.markdown(donut_card_html(c), unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3, gap="medium")
    col1.markdown(waiting_card_html(pending), unsafe_allow_html=True)
    col2.markdown(stopped_card_html(today_events), unsafe_allow_html=True)
    col3.markdown(worked_on_card_html(today_events), unsafe_allow_html=True)

    render_toasts(events)
    render_activity(events)


live()
