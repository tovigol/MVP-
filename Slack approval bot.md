# Slack approval bot

## Context
The user is taking the Person 4 role (dashboard, Slack, pitch) and wants to start with Slack.
They asked for planning only, on a personal branch, and for the uncommitted work to be discarded.
The Slack app is already created in the workspace.
Spec reference: README.md section 4 (Dashboard and Slack row), 5.4 (tables), 5.5 (approval flow), 9 (Person 4 list), 10 (slack_bolt, Socket Mode, xapp-/xoxb-, chat:write).

## Step 1 - Slack app setup checklist (in the Slack UI, by the user)
- Socket Mode: ON. App-level token with `connections:write` -> `SLACK_APP_TOKEN` (xapp-).
- Interactivity & Shortcuts: ON (no Request URL needed with Socket Mode).
- OAuth scope: `chat:write` -> reinstall app -> `SLACK_BOT_TOKEN` (xoxb-).
- Create `#agent-approvals`, run `/invite @<bot name>`, copy the channel ID -> `SLACK_APPROVAL_CHANNEL`.
- Put the three values in the local `.env` (never committed).

## Step 2 - Bot design
**Process:** `slack_bot/bot.py`, run from the repo root with `python slack_bot/bot.py`. Separate process; talks to the Gateway only through guard.db via `shared.store` (no raw SQL).
Dependencies: `slack_bolt`, `python-dotenv`.

**Data from the Gateway (shared contract):**
- `store.pending_approvals()` rows have `approval_id`, `tool`, `category`, `args_json`, `facts_json`, `reason`.
- `facts_json` replaces `amount_usd`. It holds the facts the rules engine checked, read with `json.loads(row["facts_json"])`:
  - payment: `{"amount": 80.0}` (already in dollars)
  - email: `{"recipients": ["yossibakery.com", "gmail.com"]}`
- Approval ids are 8 characters long; show them in full.
- A `reason` starting with `[DESTRUCTIVE]` marks a destructive action.

**Two loops:**
- *Poller thread (every 1s):* `store.pending_approvals()` -> post each new one with `chat_postMessage`; remember `approval_id -> (channel, ts)` in memory. For remembered messages, if the row was decided outside Slack, update the message. In particular, if the Gateway expired it (status `rejected`, `decided_by="timeout"`), change it to "Expired" and remove the buttons.
- *Socket Mode handler:* button clicks -> `store.decide_approval(approval_id, "approved" | "rejected", decided_by)` -> `chat_update` replaces the buttons with the result. If it returns `False`, the row was already decided, so show "already handled".

**Message layout (Block Kit):**
```
🟡 Approval needed            (🔴 Destructive action... if reason starts with [DESTRUCTIVE])
The assistant wants to make a payment of $80.00 / send an email to yossibakery.com, gmail.com
Why it needs approval: <reason, without the [DESTRUCTIVE] tag>
[ Approve ] (green)   [ Don't allow ] (red)
Requested 14:03 · expires after 1 minute
Technical details · tool · agent · id 1a2b3c4d
Arguments: ```{ pretty JSON, truncated ~1200 chars }```
```
After a click: header becomes "✅ Approved" / "🚫 Blocked", buttons removed, "Approved by @user at 14:03".
On expiry: "⏱️ Expired - nothing was done", buttons removed.
"Always allow this tool" button: **left out**. Rules can't target a single tool yet; check with the Gateway owner before building it. When it is built, write policy.json to a temp file first and then `os.replace` it, so the Gateway never reads half a file.

**Edge cases to handle:**
- Late click after expiry -> `decide_approval` returns `False`; message shows "Expired"; ephemeral "Already handled" to the clicker.
- Two people click -> only the first applies; the second sees the real result and an ephemeral "Already handled".
- Bot restart -> re-posts approvals still in flight; skips rows older than ~75s (Gateway gone).
- Escape `& < >` and triple backticks in arguments.

**Env:** `SLACK_APPROVAL_CHANNEL=` in `.env.example`.

## Verification
1. Unit check without Slack: build the blocks for a fake approval dict (with `facts_json`) and print the JSON; paste it into Slack Block Kit Builder to see the layout. Check a `[DESTRUCTIVE]` reason gives the red header.
2. Local end-to-end without the Gateway: script that does `log_event` + `create_approval` with temp data, run the bot, click Approve in Slack, confirm `approvals.status = approved` and that `await wait_for_approval(...)` returns "approved".
3. Expiry path: create an approval, don't click, let the Gateway mark it rejected with `decided_by="timeout"`; confirm the message changes to "Expired" with no buttons and a late click shows "Already handled".
4. Remove test rows from guard.db afterwards (local, gitignored).
