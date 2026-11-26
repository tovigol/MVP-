"""AgentGuard Slack approval bot (Person 4). Spec: README.md sections 4 and 5.5.

Flow:
  1. Every second, read pending approvals from guard.db and post a message
     with Approve / Don't allow buttons to the approvals channel.
  2. A click updates the approvals row (shared.store.decide_approval) and
     replaces the buttons with the result.
  3. If nobody answers within 60 seconds, the Gateway marks the approval
     rejected with decided_by="timeout"; the message becomes "Expired".

Run from the repo root:  python slack_bot/bot.py
Needs in .env: SLACK_BOT_TOKEN (xoxb-), SLACK_APP_TOKEN (xapp-), SLACK_APPROVAL_CHANNEL.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# Make the repo root importable (for `shared`) when run as `python slack_bot/bot.py`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from shared import store

load_dotenv(REPO_ROOT / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("slack_bot")

CHANNEL = os.environ.get("SLACK_APPROVAL_CHANNEL") or "#agent-approvals"
POLL_SECONDS = 1.0
# Skip rows older than this: the Gateway that created them is gone (e.g. crashed mid-wait).
STALE_AFTER_SECONDS = store.APPROVAL_TIMEOUT_SECONDS + 15
ARGS_PREVIEW_CHARS = 1200

TIMEOUT_MINUTES_TEXT = "1 minute" if store.APPROVAL_TIMEOUT_SECONDS == 60 else f"{store.APPROVAL_TIMEOUT_SECONDS} seconds"

# Plain-language wording for a business owner with no technical background.
HEADERS = {
    "pending": "🟡 Your AI assistant is asking for permission",
    "destructive": "🔴 Your AI assistant wants to do something that can't be undone",
    "approved": "✅ You approved this",
    "rejected": "🚫 You blocked this",
    "expired": "⏱️ Expired - nothing was done",
}

ALREADY_HANDLED_TEXT = {
    "approved": "Already handled - someone approved this request, so your click didn't change anything.",
    "rejected": "Already handled - someone blocked this request, so your click didn't change anything.",
    "expired": "Already handled - the time to answer ran out, so nothing was done. Ask the assistant to try again if it's still needed.",
}

DESTRUCTIVE_PREFIX = "[DESTRUCTIVE]"
TIMEOUT_DECIDER = "timeout"  # decided_by value the Gateway writes when nobody answered

# What the assistant wants to do, by tool category.
ACTION_TEXT = {
    "payment": "make a payment",
    "email_send": "send an email",
    "read": "look up some information",
    "bulk_export": "export a large amount of your data",
}

# approval_id -> (channel_id, message_ts) for messages still showing buttons
posted: dict[str, tuple[str, str]] = {}
posted_lock = threading.Lock()


# --- message design -----------------------------------------------------------

def _esc(text) -> str:
    """Escape text for Slack mrkdwn."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_destructive(item: dict) -> bool:
    return (item.get("reason") or "").startswith(DESTRUCTIVE_PREFIX)


def _reason_text(item: dict) -> str:
    """The reason without the [DESTRUCTIVE] tag (the red header already shows it)."""
    reason = item.get("reason") or ""
    if reason.startswith(DESTRUCTIVE_PREFIX):
        reason = reason[len(DESTRUCTIVE_PREFIX):].strip()
    return reason


def _facts(item: dict) -> dict:
    """The facts the Gateway checked, e.g. {"amount": 80.0} or {"recipients": [...]}."""
    try:
        facts = json.loads(item.get("facts_json") or "{}")
    except (TypeError, ValueError):
        return {}
    return facts if isinstance(facts, dict) else {}


def final_state(row: dict) -> str | None:
    """approved | rejected | expired once decided, None while still pending."""
    if row.get("status") == "pending" and row.get("outcome") is None:
        return None
    if row.get("decided_by") == TIMEOUT_DECIDER or row.get("outcome") == "timeout":
        return "expired"
    if row.get("status") in ("approved", "rejected"):
        return row["status"]
    return "expired"  # Gateway finished without a decision (e.g. error)


def _args_preview(args_json: str | None) -> str:
    try:
        text = json.dumps(json.loads(args_json or "{}"), indent=2, ensure_ascii=False)
    except (TypeError, ValueError):
        text = args_json or ""
    if len(text) > ARGS_PREVIEW_CHARS:
        text = text[:ARGS_PREVIEW_CHARS] + "\n…"
    return _esc(text.replace("```", "'''"))


def _hm(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%H:%M")
    except (TypeError, ValueError):
        return datetime.now().strftime("%H:%M")


def summary_sentence(item: dict) -> str:
    """One plain sentence: what the assistant wants to do."""
    category = item.get("category")
    action = ACTION_TEXT.get(category)
    if action is None:
        return f"The assistant wants to use an action it hasn't used before (`{_esc(item['tool'])}`)."
    text = f"The assistant wants to *{action}*"
    facts = _facts(item)
    amount = facts.get("amount")
    if category == "payment" and isinstance(amount, (int, float)):
        text += f" of *${amount:,.2f}*"
    if category == "email_send":
        recipients = [str(r) for r in facts.get("recipients") or []]
        if recipients:
            text += " to " + ", ".join(f"*{_esc(r)}*" for r in recipients[:5])
            if len(recipients) > 5:
                text += f" and {len(recipients) - 5} more"
    return text + "."


def why_sentence(item: dict) -> str:
    """Why the owner is being asked, without internal terms."""
    reason = _reason_text(item)
    if item.get("rule_id"):
        return f"Your rule says: _{_esc(reason)}_" if reason else "One of your rules says to check with you first."
    if reason.startswith("Tool not in registry"):
        return "This is a new kind of action for your assistant, so AgentGuard checks with you first."
    return "None of your rules cover this action, so AgentGuard checks with you first."


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def build_blocks(item: dict, state: str = "pending", actor: str | None = None) -> list[dict]:
    """Block Kit layout for one approval. state: pending | approved | rejected | expired."""
    risky = state == "pending" and _is_destructive(item)
    header = HEADERS["destructive"] if risky else HEADERS[state]

    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
        _section(summary_sentence(item)),
        _section(f"*Why am I being asked?*\n{why_sentence(item)}"),
    ]

    if state == "pending":
        blocks.append(_section(
            "*What happens next?*\n"
            "• *Approve* - the assistant does it right away.\n"
            "• *Don't allow* - the assistant won't do it and will tell you it was blocked.\n"
            f"• No answer within *{TIMEOUT_MINUTES_TEXT}* - nothing happens."
        ))
        blocks.append({
            "type": "actions",
            "block_id": "approval_actions",
            "elements": [
                {"type": "button", "action_id": "approve", "style": "primary",
                 "text": {"type": "plain_text", "text": "✅ Approve", "emoji": True},
                 "value": item["approval_id"]},
                {"type": "button", "action_id": "reject", "style": "danger",
                 "text": {"type": "plain_text", "text": "🚫 Don't allow", "emoji": True},
                 "value": item["approval_id"]},
            ],
        })
        blocks.append(_context(f"Asked at {_hm(item.get('ts'))} · please answer within {TIMEOUT_MINUTES_TEXT}"))
    elif state == "approved":
        who = actor or _esc(item.get("decided_by") or "someone")
        blocks.append(_section(f"{who} approved this at {_hm(item.get('decided_at'))}. The assistant went ahead."))
    elif state == "rejected":
        who = actor or _esc(item.get("decided_by") or "someone")
        blocks.append(_section(f"{who} blocked this at {_hm(item.get('decided_at'))}. The assistant did *not* do it."))
    else:
        blocks.append(_section(
            f"Nobody answered within {TIMEOUT_MINUTES_TEXT}, so the assistant did *not* do this.\n"
            "If it's still needed, ask the assistant to try again."
        ))

    # Technical details last, small, for whoever helps you with the setup.
    blocks.append({"type": "divider"})
    blocks.append(_context(
        f"Technical details · action `{_esc(item['tool'])}`"
        + (f" · agent {_esc(item['agent'])}" if item.get("agent") else "")
        + f" · reference {item['approval_id']}"
    ))
    blocks.append(_context(f"```{_args_preview(item.get('args_json'))}```"))
    return blocks


def fallback_text(item: dict, state: str = "pending") -> str:
    """Plain text for notifications and clients that cannot render blocks."""
    sentence = summary_sentence(item).replace("*", "").replace("`", "")
    return f"{HEADERS[state]}: {sentence}"


# --- Slack app ----------------------------------------------------------------

def create_app() -> App:
    app = App(token=os.environ["SLACK_BOT_TOKEN"])

    def decide(body: dict, client, status: str) -> None:
        approval_id = body["actions"][0]["value"]
        user = body["user"]
        user_name = user.get("username") or user.get("name") or user["id"]
        channel, ts = body["channel"]["id"], body["message"]["ts"]

        applied = store.decide_approval(approval_id, status, user_name)
        row = store.get_approval(approval_id)
        with posted_lock:
            posted.pop(approval_id, None)
        if row is None:
            return

        if applied:
            state, actor = status, f"<@{user['id']}>"
        else:
            # decide_approval returned False: someone else clicked first, or it expired.
            state, actor = final_state(row) or "expired", None

        client.chat_update(channel=channel, ts=ts, text=fallback_text(row, state),
                           blocks=build_blocks(row, state, actor))
        if not applied:
            client.chat_postEphemeral(channel=channel, user=user["id"],
                                      text=ALREADY_HANDLED_TEXT[state])
        log.info("approval %s -> %s by %s (applied=%s)", approval_id, state, user_name, applied)

    @app.action("approve")
    def on_approve(ack, body, client):
        ack()
        decide(body, client, "approved")

    @app.action("reject")
    def on_reject(ack, body, client):
        ack()
        decide(body, client, "rejected")

    return app


# --- polling ------------------------------------------------------------------

def _is_stale(item: dict) -> bool:
    try:
        age = (datetime.now() - datetime.fromisoformat(item["ts"])).total_seconds()
    except (TypeError, ValueError):
        return False
    return age > STALE_AFTER_SECONDS


def _post(client, item: dict) -> None:
    resp = client.chat_postMessage(channel=CHANNEL, text=fallback_text(item),
                                   blocks=build_blocks(item))
    with posted_lock:
        posted[item["approval_id"]] = (resp["channel"], resp["ts"])
    log.info("posted approval %s for %s", item["approval_id"], item["tool"])


def _sync_posted(client) -> None:
    """Update messages decided outside Slack (mainly: expired after 60s) and drop their buttons."""
    with posted_lock:
        ids = list(posted)
    for approval_id in ids:
        row = store.get_approval(approval_id)
        state = final_state(row) if row else None
        if row is not None and state is None:
            continue  # still waiting for an answer
        with posted_lock:
            entry = posted.pop(approval_id, None)
        if entry and row is not None:
            client.chat_update(channel=entry[0], ts=entry[1], text=fallback_text(row, state),
                               blocks=build_blocks(row, state))
            log.info("approval %s -> %s (outside Slack)", approval_id, state)


def poll_loop(client) -> None:
    while True:
        try:
            for item in store.pending_approvals():
                with posted_lock:
                    already = item["approval_id"] in posted
                if not already and not _is_stale(item):
                    _post(client, item)
            _sync_posted(client)
        except Exception:
            log.exception("poll loop error")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    app = create_app()
    threading.Thread(target=poll_loop, args=(app.client,), daemon=True, name="poll").start()
    log.info("AgentGuard Slack bot running, posting to %s", CHANNEL)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
