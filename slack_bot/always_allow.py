"""The "Always allow" button in Slack: writes a category-wide allow rule to shared/policy.json.

The rules engine matches rules by category only, so "always allow" means the whole
category (every payment, every email...), not just the one tool that asked. The Slack
messages say this to the owner in plain words (scope_text).

The rule goes first in `rules` (first match wins), so it also overrides the category's
limits. The Gateway re-reads policy.json on every call, so it takes effect right away.
Undo: re-run onboarding (it rebuilds `rules`) or delete the rule from policy.json.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

from shared.policy import POLICY_PATH

DESTRUCTIVE_PREFIX = "[DESTRUCTIVE]"

# Categories where the button is offered. Never: bulk_export (README iron rule, export is
# never allowed) and unknown (would switch off fail-closed for every new, unclassified tool).
ALLOWABLE_CATEGORIES = ("payment", "email_send", "read")

# What the owner allows, in plain words: "your assistant may <scope> without asking you".
# Slack mrkdwn (*bold*); scope_text(plain=True) strips it for the confirm dialog.
SCOPE_TEXT = {
    "payment": "make *any payment, of any amount* (even above the limits you set)",
    "email_send": "send *any email, to anyone* (including people outside your business)",
    "read": "look up *any information* in your connected tools",
}

# Short noun for the dialog title: "Always allow payments?"
CATEGORY_NOUN = {"payment": "payments", "email_send": "emails", "read": "look-ups"}

_write_lock = threading.Lock()


def can_always_allow(item: dict) -> bool:
    """True if the Slack message may offer "Always allow" for this approval."""
    if (item.get("reason") or "").startswith(DESTRUCTIVE_PREFIX):
        return False  # can't be undone: one-time approval only
    return item.get("category") in ALLOWABLE_CATEGORIES


def rule_id_for(category: str) -> str:
    """'email_send' -> 'always-allow-email-send' (kebab-case, like onboarding's ids)."""
    return "always-allow-" + category.replace("_", "-")


def scope_text(category: str, plain: bool = False) -> str:
    text = SCOPE_TEXT.get(category, f"do *every action of type {category}*")
    return text.replace("*", "") if plain else text


def category_noun(category: str) -> str:
    return CATEGORY_NOUN.get(category, "this kind of action")


def has_rule(category: str, path: Path = POLICY_PATH) -> bool:
    rule_id = rule_id_for(category)
    with open(path, encoding="utf-8") as f:
        return any(r.get("id") == rule_id for r in json.load(f).get("rules", []))


def add_always_allow(category: str, tool: str, decided_by: str,
                     path: Path = POLICY_PATH, now: datetime | None = None) -> bool:
    """Adds the allow rule at the top of policy.json. Returns False if it was already there.

    Raises ValueError for a category that must never be allowed, and OSError/ValueError
    if policy.json can't be read or written (the caller tells the owner).
    """
    if category not in ALLOWABLE_CATEGORIES:
        raise ValueError(f"category {category!r} can't be always allowed")
    path = Path(path)
    rule_id = rule_id_for(category)
    when = (now or datetime.now()).strftime("%Y-%m-%d %H:%M")
    with _write_lock:
        with open(path, encoding="utf-8") as f:
            policy = json.load(f)
        rules = policy.setdefault("rules", [])
        if not isinstance(rules, list):
            raise ValueError("policy.json 'rules' is not a list")
        if any(isinstance(r, dict) and r.get("id") == rule_id for r in rules):
            return False
        rules.insert(0, {
            "id": rule_id,
            "category": category,
            "effect": "allow",
            "when": {},
            "explain": f"Always allowed by {decided_by} from Slack on {when} (after approving {tool})",
            "source": "slack",
        })
        # Atomic write, same pattern as onboarding/translate.py::save_policy.
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    return True
