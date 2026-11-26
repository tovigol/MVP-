# Slack approval bot

## Context
The user is taking the Person 4 role (dashboard, Slack, pitch) and wants to start with Slack.
They asked for planning only, on a personal branch, and for the uncommitted work to be discarded.
The Slack app is already created in the workspace.
Spec reference: README.md section 4 (Dashboard and Slack row), 5.4 (tables), 5.5 (approval flow), 9 (Person 4 list), 10 (slack_bolt, Socket Mode, xapp-/xoxb-, chat:write).

## Step 1 - Branch and clean up (git only)
1. `git checkout -b person4-slack` (from `main` at `764324e`).
2. `git restore shared/store.py` - discards the only uncommitted change (pending_approvals / get_approval / decide_approval edits). Nothing else is uncommitted; `slack_bot.py` was never written.
3. Branch stays local until the first real commit; then `git push -u origin person4-slack`.

## Step 2 - Slack app setup checklist (in the Slack UI, by the user)
- Socket Mode: ON. App-level token with `connections:write` -> `SLACK_APP_TOKEN` (xapp-).
- Interactivity & Shortcuts: ON (no Request URL needed with Socket Mode).
- OAuth scope: `chat:write` -> reinstall app -> `SLACK_BOT_TOKEN` (xoxb-).
- Create `#agent-approvals`, run `/invite @<bot name>`, copy the channel ID -> `SLACK_APPROVAL_CHANNEL`.
- Put the three values in the local `.env` (never committed).

## Step 3 - Bot design (to implement after approval)
**Process:** `slack_bot.py` at repo root, run with `python slack_bot.py`. Separate process; talks to the Gateway only through guard.db via `shared.store`.
Dependencies: `slack_bolt`, `python-dotenv` (not installed yet).

**Two loops:**
- *Poller thread (every 1s):* `store.pending_approvals()` -> post each new one with `chat_postMessage`; remember `approval_id -> (channel, ts)` in memory. For remembered messages, if the Gateway timed out, edit the message to "Timed out".
- *Socket Mode handler:* button clicks -> `store.decide_approval(id, status, user)` -> `chat_update` replaces the buttons with the result.

**Message layout (Block Kit):**
```
🟡 Approval needed            (🔴 Destructive action... if registry risk = destructive)
Tool: stripe_x    Category: payment    Amount: $500.00    Agent: ...    Risk: write
Why it needs approval: <decision.reason>
Arguments: ```{ pretty JSON, truncated ~1200 chars }```
[ Approve ] (green)   [ Reject ] (red)
Requested 14:03:21 · expires after 60s · id 1a2b3c4d
```
After a click: header becomes "✅ Approved" / "❌ Rejected", buttons removed, footer "Approved by @user at 14:03:40".
On timeout: "⏱️ Timed out - the agent was told to stop".
"Always allow this tool" button: **left out** (user decision). Rules match only by category, so it would allow the whole category; raise with the team.

**Edge cases to handle:**
- Late click after timeout -> not applied; message shows "Timed out"; ephemeral "Too late" to the clicker.
- Two people click -> only the first applies; the second sees the real result.
- Bot restart -> re-posts approvals still in flight; skips rows older than ~75s (Gateway gone).
- Escape `& < >` and triple backticks in arguments.

**Required changes in shared/store.py (shared contract - tell the team):**
- `pending_approvals()`: also filter `events.outcome IS NULL`, so timed-out rows are not posted.
- New `get_approval(approval_id)`: approval row joined with its event (status, outcome, tool, amount...).
- `decide_approval()`: only update while the event outcome is still NULL (Gateway still waiting).

**Env:** add `SLACK_APPROVAL_CHANNEL=` to `.env.example`.

## Verification
1. Unit check without Slack: build the blocks for a fake approval dict and print the JSON; paste it into Slack Block Kit Builder to see the layout.
2. Local end-to-end without the Gateway: script that does `log_event` + `create_approval` with temp data, run the bot, click Approve in Slack, confirm `approvals.status = approved` and that `await wait_for_approval(...)` returns "approved" and sets the event outcome.
3. Timeout path: create an approval, don't click, confirm the message changes to "Timed out" after 60s and a late click shows "Too late".
4. Remove test rows from guard.db afterwards (local, gitignored).
