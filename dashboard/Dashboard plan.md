# Dashboard (live log)

## Context
- Person 4 role, second task after the Slack bot: the live log dashboard.
- Spec: README §2 (must-have 5 and the success metric), §4 (Dashboard row), §5.4 (tables), §8 step 7 (summary screen), §9 (Person 4: refresh every 2s, colored by decision, counters on top), §10 (Streamlit is allowed).
- Framework: Streamlit, as a separate process. Onboarding (Person 3) keeps port 8000, and the dashboard runs on 8501.

## Step 1 - Setup
- Dependencies: `streamlit` (>= 1.37 for `st.fragment(run_every=...)`) and `pandas`.
- Run from the repo root: `streamlit run dashboard/app.py --server.port 8501`.
- Streamlit puts the script's folder on `sys.path`, not the repo root, so `app.py` adds the repo root before `from shared import store, policy`.
- Read-only: no writes to guard.db and no raw SQL. All reads go through `shared.store`. `GUARD_DB` env var is honored automatically by `store.DB_PATH`.

## Step 2 - Design
**Files:** `dashboard/__init__.py`, `dashboard/app.py` (UI), `dashboard/data.py` (pure functions: rows -> DataFrame, counters, colors; testable without Streamlit), `dashboard/seed_demo.py` (fake data).

**Data sources (existing functions, reuse them):**
- `store.recent_events(limit)` gives the newest events first (`shared/store.py:116`).
- `store.pending_approvals()` gives approvals the Gateway is still waiting on (`shared/store.py:141`).
- `store.daily_payment_total()` gives today's spent dollars (`shared/store.py:68`).
- `policy.load_policy()` gives the daily limit from the `pay-small` rule's `daily_total_usd_lte` and the business name for the title.
- Amount: use `amount_usd`. If a `facts_json` column exists (the Gateway contract change), fall back to `facts["amount"]`. Recipients come from `facts["recipients"]`.

**Layout (top to bottom):**
```
AgentGuard · yossi-bakery                         ● Live (updated 14:03:12)  [Pause]
[ ✅ Automatic 12 (75%) ] [ 🚫 Blocked 3 ] [ 🟡 Reached owner 1 ] [ 💵 Today $180 / $300 ]
⏳ Waiting for approval in Slack: email_send_email → gmail.com · id 1a2b3c4d · 42s left
Time | Agent | Tool | Category | Amount | Decision | Outcome | Rule | Why
14:03 | demo | stripe_create_payment_link | payment | $80.00 | allow | forwarded | pay-small | ...
```
- **Counters** cover today's events. *Automatic* means outcome `forwarded`. *Blocked* means outcome `blocked`. *Reached owner* means effect `ask` (approved, rejected, timeout or still pending), with a small breakdown below it. Show automatic as a % of all, because this is the pitch's success metric (README §2).
- **Row colors** (pandas Styler, light background): green for forwarded, red for blocked, amber for pending ask (outcome NULL), blue for approved, gray for rejected or timeout, dark red for error. A reason starting with `[DESTRUCTIVE]` gets a 🔴 in the "Why" column.
- **Pending panel**: one line per `pending_approvals()` row, with seconds left = 60 - age, from `store.APPROVAL_TIMEOUT_SECONDS`. Read-only, because decisions happen in Slack.
- **Live refresh**: the counters, pending panel and table are inside one `@st.fragment(run_every=2)`, so only that part reruns and the page doesn't flicker. A "Pause" toggle in `st.session_state` stops the refresh during the pitch.
- **New-event toast**: remember the last seen event id in `session_state`. For a new `blocked` event, show `st.toast("🚫 Blocked: <tool>")`. This is what the judges notice in demo step 3.
- **Details**: a selectbox of the visible event ids shows the pretty `args_json` (truncated at about 1200 chars) and the full reason.
- **Sidebar**: decision filter (all/allow/deny/ask) and number of rows (50/100/500).

**Edge cases:**
- Empty DB or missing guard.db: `store.connect()` creates the tables. Show "No agent activity yet".
- Counting today's events with `recent_events(500)` can undercount on a very busy day. That is fine for the demo. If needed, ask Person 1 for a read-only `store.events_since(day)` helper instead of adding raw SQL.
- Old timestamps without `facts_json`, or args that aren't JSON: show them as raw text and never crash the fragment.
- policy.json missing or without a daily limit: show the spent amount without "/ limit".

**Extras (only after the must-haves, README §2):**
- A red-team score card: read the score `redteam.py` writes (agree on the file format with Person 2).
- A bypass-detector alerts list (once Person 1 or 4 builds it).
- A "learning" suggestion card ("You approved 3 emails to X") that links to Person 3's suggestion flow.

## Verification
1. `dashboard/data.py` unit check: build the DataFrame and counters from a list of fake event dicts and assert the counts and colors (no Streamlit needed).
2. Seed data: `GUARD_DB=demo.db python dashboard/seed_demo.py` writes about 15 events (allow, deny, ask approved, ask timeout, one `[DESTRUCTIVE]`, one pending approval) with `store.log_event` + `store.create_approval` + `store.update_outcome`. Then run `GUARD_DB=demo.db streamlit run dashboard/app.py --server.port 8501` and check the colors, counters and pending countdown.
3. Live update: while the dashboard is open, run the seed script again (or `slack_bot/live_test.py`) and check that the new rows and a "Blocked" toast show up within about 2s with no page flicker.
4. Together with Slack: create a pending approval, see it in the panel, click Approve in Slack, and check that the row turns blue and leaves the panel.
5. Delete `demo.db` afterwards. Use only test data; never point the seed script at the real guard.db.
