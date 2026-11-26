"""AgentGuard deterministic policy engine (no LLM). Fail-closed.

policy.json schema (rules are evaluated top-to-bottom, first match wins):
    {
      "business": str,
      "company_domains": [str],
      "default_effect": "allow" | "deny" | "ask",
      "rules": [
        {
          "id": str,
          "category": "payment" | "email_send" | "read" | "bulk_export",
          "when": {<operator>: <value>, ...},   # all conditions must hold; {} = always
          "effect": "allow" | "deny" | "ask",
          "explain_he": str
        }
      ]
    }
    Supported `when` operators (any other operator -> deny):
      amount_usd_lte:      number     payment amount in USD <= value
      daily_total_usd_lte: number     today's total + this payment (USD) <= value
      all_recipients_in:   [domain]   every recipient's domain is in the list
      any_recipient_in:    [domain]   at least one recipient's domain is in the list
    TODO: populate `rules` via Onboarding or tests.

registry.json schema (tool name -> entry):
    {
      "<tool_name>": {
        "category": "payment" | "email_send" | "read" | "bulk_export",
        "amount_field": str,            # payment tools only
        "amount_unit": "cents" | "usd", # payment tools only
        "recipient_fields": [str]       # email_send tools only, e.g. ["to", "cc", "bcc"]
      }
    }
    TODO: populate from the downstream server's list_tools.
    Any tool not in registry.json is denied.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent
POLICY_PATH = BASE_DIR / "policy.json"
REGISTRY_PATH = BASE_DIR / "registry.json"

OPERATORS = {"amount_usd_lte", "daily_total_usd_lte", "all_recipients_in", "any_recipient_in"}
UNIT_DIVISORS = {"cents": 100, "usd": 1}
EFFECTS = ("allow", "deny", "ask")

AGENT_MESSAGES = {
    "allow": "Allowed by policy rule '{rule}'.",
    "ask": "Requires owner approval (rule '{rule}'). Wait for the decision; do not retry or work around it.",
    "deny": "Denied by policy (rule '{rule}'). Do not retry or attempt a workaround.",
}


@dataclass
class Decision:
    effect: Literal["allow", "deny", "ask"]
    rule_id: str | None
    reason_he: str
    reason_for_agent: str


def load_policy() -> dict:
    """Reads policy.json from disk on every call (no caching)."""
    with open(POLICY_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


def _deny(reason_he: str, reason_for_agent: str, rule_id: str | None = None) -> Decision:
    return Decision("deny", rule_id, reason_he, reason_for_agent)


def normalize_amount_usd(raw, unit: str) -> float:
    """Converts a raw amount to USD. Raises ValueError/TypeError if invalid."""
    if unit not in UNIT_DIVISORS or raw is None or isinstance(raw, bool):
        raise ValueError("invalid amount")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError("invalid amount")
    return value / UNIT_DIVISORS[unit]


def collect_recipients(args: dict, fields: list[str]) -> list[str]:
    """Lowercased recipient addresses from the given fields (str, comma-separated str, or list)."""
    recipients = []
    for field in fields:
        value = args.get(field)
        if value is None:
            continue
        items = [value] if isinstance(value, str) else value
        for item in items:
            if not isinstance(item, str):
                raise ValueError("invalid recipient")
            for addr in item.split(","):
                addr = addr.strip().lower()
                if addr:
                    recipients.append(addr)
    return recipients


def _domain(addr: str) -> str:
    local, sep, domain = addr.rpartition("@")
    if not sep or not local or not domain:
        raise ValueError("invalid recipient")
    return domain


def _in_domains(addr: str, domains: list[str]) -> bool:
    # Exact match on the domain after "@", so "domain.com.evil.io" != "domain.com"
    return _domain(addr) in {d.lower() for d in domains}


def _matches(when: dict, amount_usd, recipients, db) -> bool:
    for op, value in when.items():
        if op not in OPERATORS:
            raise ValueError(f"unsupported operator: {op}")
        if op == "amount_usd_lte":
            if amount_usd is None or amount_usd > float(value):
                return False
        elif op == "daily_total_usd_lte":
            if amount_usd is None or db.daily_total_usd() + amount_usd > float(value):
                return False
        elif op == "all_recipients_in":
            if not recipients or not all(_in_domains(r, value) for r in recipients):
                return False
        elif op == "any_recipient_in":
            if not recipients or not any(_in_domains(r, value) for r in recipients):
                return False
    return True


def _evaluate(tool_name: str, args: dict, policy: dict, registry: dict, db) -> Decision:
    tool = registry.get(tool_name)
    if tool is None:
        return _deny(
            f"הכלי '{tool_name}' אינו רשום - נחסם",
            f"Tool '{tool_name}' is not registered. Denied.",
        )
    category = tool["category"]

    amount_usd = None
    if category == "payment":
        try:
            amount_usd = normalize_amount_usd(args.get(tool["amount_field"]), tool["amount_unit"])
        except (ValueError, TypeError):
            return _deny("סכום התשלום אינו תקין - נחסם", "Payment amount is missing or invalid. Denied.")

    recipients = None
    if category == "email_send":
        recipients = collect_recipients(args, tool["recipient_fields"])
        if not recipients:
            return _deny("לא צוינו נמענים - נחסם", "Email has no recipients. Denied.")
        for r in recipients:
            _domain(r)  # validates address format; raises -> deny

    for rule in policy["rules"]:
        if rule["category"] != category:
            continue
        if _matches(rule.get("when", {}), amount_usd, recipients, db):
            effect = rule["effect"]
            if effect not in EFFECTS:
                raise ValueError(f"invalid effect: {effect}")
            return Decision(
                effect,
                rule["id"],
                rule.get("explain_he", ""),
                AGENT_MESSAGES[effect].format(rule=rule["id"]),
            )

    default = policy.get("default_effect", "deny")
    if default not in EFFECTS:
        default = "deny"
    return Decision(
        default,
        None,
        "אין כלל מתאים - ברירת מחדל",
        f"No policy rule matched. Default effect: {default}.",
    )


def evaluate(tool_name: str, args: dict, policy: dict, registry: dict, db) -> Decision:
    """Deterministic evaluation. Any exception results in deny.

    `db` must provide daily_total_usd() -> float (pass the `store` module).
    """
    try:
        return _evaluate(tool_name, args, policy, registry, db)
    except Exception as e:
        return _deny(
            "שגיאה בבדיקת המדיניות - נחסם",
            f"Policy evaluation error ({type(e).__name__}). Denied.",
        )
