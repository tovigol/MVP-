"""Tests for the "Always allow" button: policy writer + Slack message layout. No Slack, no network."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.policy import evaluate  # noqa: E402
from slack_bot import always_allow  # noqa: E402
from slack_bot import bot  # noqa: E402

REGISTRY = {
    "stripe_create_payment_link": {"category": "payment", "risk": "write",
                                   "amount_field": "amount", "amount_unit": "cents"},
    "email_send_email": {"category": "email_send", "risk": "write",
                         "recipient_fields": ["to", "cc", "bcc"]},
}

BASE_POLICY = {
    "business": "yossi-bakery",
    "company_domains": ["yossibakery.com"],
    "default_by_risk": {"read": "allow", "write": "ask", "destructive": "ask"},
    "rules": [
        {"id": "pay-small", "category": "payment", "effect": "allow",
         "when": {"amount_usd_lte": 100}, "explain": "Payments up to $100"},
        {"id": "pay-medium", "category": "payment", "effect": "ask",
         "when": {"amount_usd_lte": 1000}, "explain": "Payments up to $1000 need approval"},
        {"id": "email-internal", "category": "email_send", "effect": "allow",
         "when": {"all_recipients_in": "company_domains"}, "explain": "Internal emails"},
    ],
}


@pytest.fixture
def policy_path(tmp_path: Path) -> Path:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(BASE_POLICY), encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- policy writer --------------------------------------------------------------

def test_rule_id_is_kebab_case():
    assert always_allow.rule_id_for("email_send") == "always-allow-email-send"


def test_adds_rule_first_and_keeps_others(policy_path):
    now = datetime(2026, 11, 27, 14, 5)
    assert always_allow.add_always_allow("email_send", "email_send_email", "yossi",
                                         path=policy_path, now=now)
    rules = _read(policy_path)["rules"]
    assert rules[0] == {
        "id": "always-allow-email-send", "category": "email_send", "effect": "allow", "when": {},
        "explain": "Always allowed by yossi from Slack on 2026-11-27 14:05 (after approving email_send_email)",
        "source": "slack",
    }
    assert rules[1:] == BASE_POLICY["rules"]
    assert not policy_path.with_name("policy.json.tmp").exists()


def test_is_idempotent(policy_path):
    assert always_allow.add_always_allow("payment", "stripe_create_payment_link", "yossi", path=policy_path)
    assert not always_allow.add_always_allow("payment", "stripe_create_payment_link", "dana", path=policy_path)
    ids = [r["id"] for r in _read(policy_path)["rules"]]
    assert ids.count("always-allow-payment") == 1
    assert always_allow.has_rule("payment", path=policy_path)


@pytest.mark.parametrize("category", ["bulk_export", "unknown", None])
def test_refuses_unsafe_categories(policy_path, category):
    with pytest.raises(ValueError):
        always_allow.add_always_allow(category, "crm_export_all", "yossi", path=policy_path)
    assert _read(policy_path) == BASE_POLICY


@pytest.mark.parametrize("category, tool, args", [
    ("email_send", "email_send_email", {"to": "new.customer@gmail.com"}),
    ("payment", "stripe_create_payment_link", {"amount": 50000}),   # $500 -> was ask
    ("payment", "stripe_create_payment_link", {"amount": 500000}),  # $5000 -> was default ask
])
def test_engine_allows_after_rule(policy_path, category, tool, args):
    before = evaluate(tool, args, _read(policy_path), REGISTRY, 0)
    assert before.effect == "ask"
    always_allow.add_always_allow(category, tool, "yossi", path=policy_path)
    after = evaluate(tool, args, _read(policy_path), REGISTRY, 0)
    assert after.effect == "allow"
    assert after.rule_id == always_allow.rule_id_for(category)


def test_bad_amount_is_still_denied(policy_path):
    """The rule removes limits, not the fail-closed checks."""
    always_allow.add_always_allow("payment", "stripe_create_payment_link", "yossi", path=policy_path)
    d = evaluate("stripe_create_payment_link", {"amount": "abc"}, _read(policy_path), REGISTRY, 0)
    assert d.effect == "deny"


# --- Slack message ----------------------------------------------------------------

def _item(category="email_send", reason="Needs approval", tool="email_send_email") -> dict:
    return {"approval_id": "a1", "tool": tool, "category": category, "reason": reason,
            "rule_id": None, "args_json": "{}", "facts_json": "{}", "ts": "2026-11-27T14:00:00",
            "agent": "agent", "decided_by": "yossi", "decided_at": "2026-11-27T14:01:00"}


def _action_ids(blocks: list[dict]) -> list[str]:
    return [e["action_id"] for b in blocks if b["type"] == "actions" for e in b["elements"]]


@pytest.mark.parametrize("category", ["payment", "email_send", "read"])
def test_button_offered_with_confirm(category):
    blocks = bot.build_blocks(_item(category))
    assert _action_ids(blocks) == ["approve", "reject", "always_allow"]
    button = next(e for b in blocks if b["type"] == "actions" for e in b["elements"]
                  if e["action_id"] == "always_allow")
    confirm = button["confirm"]
    text = confirm["text"]["text"]
    assert confirm["text"]["type"] == "plain_text"  # dialogs don't render mrkdwn
    assert always_allow.scope_text(category, plain=True) in text
    assert "*" not in text and "`" not in text
    assert len(text) <= 300 and len(confirm["title"]["text"]) <= 100  # Slack limits
    assert "Always allow" in json.dumps(blocks)


@pytest.mark.parametrize("item", [
    _item("bulk_export", tool="crm_export_all"),
    _item("unknown", tool="stripe_delete_customer"),
    _item("payment", reason="[DESTRUCTIVE] Refund"),
])
def test_button_not_offered(item):
    blocks = bot.build_blocks(item)
    assert _action_ids(blocks) == ["approve", "reject"]
    assert "Always allow" not in json.dumps(blocks)


@pytest.mark.parametrize("state", ["approved", "always", "rejected", "expired"])
def test_decided_states_have_no_buttons(state):
    assert _action_ids(bot.build_blocks(_item(), state)) == []


def test_always_state_explains_scope():
    text = json.dumps(bot.build_blocks(_item("email_send"), "always", "<@U1>"), ensure_ascii=False)
    assert "From now on" in text
    assert "always-allow-email-send" in text
    assert bot.fallback_text(_item(), "always").startswith(bot.HEADERS["always"])
