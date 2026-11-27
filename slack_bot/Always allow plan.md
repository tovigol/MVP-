# Plan: "Always allow" button in the Slack approval bot

## Context
- Person 4 task, README §5.5 step 2: next to *Approve* / *Don't allow*, add a third button that writes an `allow` rule to `shared/policy.json`, so the same kind of action never asks the owner again. The Gateway re-reads `policy.json` on every call ([gateway.py:76](gateway.py#L76) → `load_policy()`), so the rule takes effect right away.
- Rules in [shared/policy.py](shared/policy.py) match **by category only** (`rule["category"] == facts["category"]`). Decision with the user: **no engine change** — the button allows the **whole category** (e.g. every email, every payment), and the Slack message must clearly explain to the owner what they are approving.
- Work goes on a new branch: `feature/slack-always-allow` (from `main`).

## Steps

### 0. Branch + plan file in the repo
- `git checkout -b feature/slack-always-allow`
- Save this plan as `slack_bot/Always allow plan.md` (same style as `Slack approval bot.md`, `dashboard/Dashboard plan.md`).

### 1. Policy writer — new `slack_bot/always_allow.py`
Small, pure-ish module so it can be unit-tested without Slack:
- `ALLOWABLE_CATEGORIES = {"payment", "email_send", "read"}` — the button is **not** offered for:
  - `bulk_export` (README iron rule + `onboarding/policy_schema.py::_validate_safety`: export is never allowed),
  - `unknown` (unregistered tools; an allow here would switch off fail-closed for *every* new tool).
  - `[DESTRUCTIVE]` requests (reason starts with `[DESTRUCTIVE]`) — can't be undone, one-time approval only.
- `rule_id_for(category) -> "always-allow-<category>"` (kebab-case, e.g. `always-allow-email-send`).
- `add_always_allow(category, tool, decided_by, path=POLICY_PATH) -> bool`:
  - load with `shared.policy.load_policy()` style (read path), skip if a rule with that id already exists (idempotent, returns False),
  - insert at **index 0** of `rules` (first match wins → it overrides the category's limits; that is exactly what the owner is told),
  - rule: `{"id", "category", "effect": "allow", "when": {}, "explain": "Always allowed by <who> from Slack on <date> (after approving <tool>)", "source": "slack"}`,
  - atomic write: tmp file + `os.replace`, same pattern as `onboarding/translate.py::save_policy` (without `validate_policy`, which would reject an unbounded email/payment allow by design),
  - guarded by a module `threading.Lock` (two clicks at once).
- `scope_text(category)` → plain-language sentences used in Slack, e.g.
  - payment: "*all payments, of any amount*, without asking you — including above your limits"
  - email_send: "*all emails, to any address*, including people outside your business"
  - read: "looking up any information"

### 2. Slack bot — [slack_bot/bot.py](slack_bot/bot.py)
- `build_blocks` (pending state): if the category is allowable and not destructive, add a third button `action_id="always_allow"`, text "♾️ Always allow", value = approval id, with a Block Kit **`confirm` dialog** that explains the scope before anything changes:
  - title "Allow this every time?", text: "From now on your assistant may do *{scope_text}* … This applies to the whole category, not only to `<tool>`. You can undo it by re-running onboarding or removing rule `<id>`.", confirm "Yes, always allow", deny "Cancel".
- Add a line to the "What happens next?" section: "• *Always allow* – approve now *and* stop asking for {scope}."
- New handler `@app.action("always_allow")`: `ack()`, then
  1. `decide(body, client, "approved")` logic (reuse; refactor `decide` to return `applied`/row),
  2. only if the approval was actually applied → `always_allow.add_always_allow(row["category"], row["tool"], user_name)`,
  3. update the message to a new state `"always"`: header "♾️ You allowed this every time", body "@user approved this and said *always allow {scope}*. Your assistant won't ask about this again. To undo: …",
  4. post a separate (non-ephemeral) channel message summarising the new rule, so the change is visible to everyone in `#agent-approvals`,
  5. if writing `policy.json` fails → the one-time approval still stands; post an ephemeral "Approved this once, but couldn't save the permanent rule: <error>".
- Add `"always"` to `HEADERS` and handle it in `build_blocks` / `fallback_text`.

### 3. Tests — new `slack_bot/test_always_allow.py`
- Policy writer (tmp `policy.json` via `tmp_path`): rule inserted at top, idempotent, file still valid JSON, original rules untouched.
- With the written policy, `shared.policy.evaluate` returns `allow` with `rule_id="always-allow-…"` for a call that used to be `ask` (e.g. email to gmail, payment of $500).
- `build_blocks`: third button + confirm dialog present for payment/email_send/read; absent for bulk_export, unknown and `[DESTRUCTIVE]`; no buttons in decided states.
- Run: `python -m pytest -q slack_bot dashboard test_policy.py`.

## Things to tell the team (not code)
- Person 3: re-running onboarding rebuilds `rules`, so "always allow" rules are dropped (that is also the undo path). The extra `"source": "slack"` key is ignored by the engine.
- Dashboard: future events show the `explain` text of the new rule, so the owner sees why they passed.

## Verification
1. `python -m pytest -q slack_bot dashboard test_policy.py` passes.
2. Live: `python slack_bot/live_test.py` style run (temp DB) → click *Always allow* on the email approval → confirm dialog shows the scope text → message turns "♾️ You allowed this every time" and `shared/policy.json` has `always-allow-email-send` at the top (restore the file afterwards).
3. With Gateway + bot running, send the demo's "quote to gmail" prompt twice: the first asks in Slack, after *Always allow* the second goes through with no Slack message and a green row on the dashboard.
