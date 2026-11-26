"""AgentGuard deterministic policy engine (no LLM). Spec: README.md sections 5.1-5.3, 6.

evaluate() is a PURE function: the Gateway reads policy.json, registry.json and
today's payment total, and passes them in. Nothing here touches disk or the DB
except load_policy() / load_registry().

Rules are checked top to bottom; the first rule whose category matches and whose
`when` conditions all hold decides. No match -> policy["default_by_risk"][risk].

Supported `when` operators (any other operator -> deny):
  amount_usd_lte:      number                         amount_usd <= value
  daily_total_usd_lte: number                         daily_total_usd + amount_usd <= value
  all_recipients_in:   [domain] | "company_domains"   every recipient's domain is listed
  any_recipient_in:    [domain] | "company_domains"   at least one recipient's domain is listed
A condition whose fact is missing (no amount, no recipients) is false.

Fail-closed:
  - A tool missing from registry.json is {"category": "unknown", "risk": "write"} -> ask.
  - An unparseable amount, a malformed recipient, or any exception -> deny.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from email.utils import getaddresses
from pathlib import Path
from typing import Literal

BASE_DIR = Path(__file__).resolve().parent
POLICY_PATH = BASE_DIR / "policy.json"
REGISTRY_PATH = BASE_DIR / "registry.json"

OPERATORS = {"amount_usd_lte", "daily_total_usd_lte", "all_recipients_in", "any_recipient_in"}
UNIT_DIVISORS = {"cents": 100, "usd": 1}
EFFECTS = ("allow", "deny", "ask")
RISKS = ("read", "write", "destructive")
UNREGISTERED_TOOL = {"category": "unknown", "risk": "write"}

# One "@", no spaces, brackets, quotes or separators; domain has at least one dot.
_ADDRESS_RE = re.compile(r"^[^@\s<>()\[\],;:\"\\]+@[a-z0-9-]+(\.[a-z0-9-]+)+$")

AGENT_MESSAGES = {
    "allow": "Allowed by policy.",
    "ask": "This action requires the owner's approval.",
    "deny": "This action is not allowed by the business policy.",
}


@dataclass
class Decision:
    effect: Literal["allow", "deny", "ask"]
    rule_id: str | None      # which rule decided; None = default
    reason: str              # for the dashboard and Slack
    reason_for_agent: str    # returned to the agent when blocked


def load_policy() -> dict:
    """Reads policy.json from disk on every call (no caching)."""
    with open(POLICY_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_registry() -> dict:
    with open(REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


# --- normalize ----------------------------------------------------------------

def _amount_usd(raw, unit) -> float:
    """Raw amount -> USD. Only real numbers are accepted (no bool, str, NaN, negatives)."""
    if unit not in UNIT_DIVISORS or isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError(f"invalid amount: {raw!r}")
    if not math.isfinite(raw) or raw < 0:
        raise ValueError(f"invalid amount: {raw!r}")
    return raw / UNIT_DIVISORS[unit]


def _recipients(args: dict, fields: list[str]) -> list[str]:
    """Lowercased addresses from every field (str, comma-separated str, or list of str)."""
    raw = []
    for field in fields:
        value = args.get(field)
        if value is None or value == "":
            continue
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list) or not all(isinstance(i, str) for i in items):
            raise ValueError(f"invalid recipient field: {field}")
        raw.extend(items)
    recipients = []
    for _name, addr in getaddresses(raw):
        addr = addr.strip().lower()
        # Empty or odd entries mean the parser was confused: refuse rather than guess.
        if not _ADDRESS_RE.match(addr):
            raise ValueError(f"invalid recipient: {addr!r}")
        recipients.append(addr)
    return recipients


def _domain(addr: str) -> str:
    return addr.rpartition("@")[2]


def normalize(tool_name: str, args: dict, registry: dict) -> dict:
    """Facts about one call. Raises ValueError on a bad amount or recipient."""
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    meta = registry.get(tool_name, UNREGISTERED_TOOL)
    category = meta.get("category", "unknown")
    risk = meta.get("risk") if meta.get("risk") in RISKS else "write"
    facts = {"category": category, "risk": risk, "registered": tool_name in registry,
             "amount_usd": None, "recipients": []}
    if category == "payment":
        facts["amount_usd"] = _amount_usd(args.get(meta.get("amount_field")), meta.get("amount_unit"))
    if meta.get("recipient_fields"):
        facts["recipients"] = _recipients(args, meta["recipient_fields"])
    return facts


def log_fields(tool_name: str, args, registry: dict) -> tuple[str, float | None]:
    """(category, amount_usd) for the events table. Never raises."""
    category = registry.get(tool_name, UNREGISTERED_TOOL).get("category", "unknown")
    try:
        return category, normalize(tool_name, args, registry)["amount_usd"]
    except Exception:
        return category, None


# --- evaluate -----------------------------------------------------------------

def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"limit must be a number: {value!r}")
    return float(value)


def _domains(value, policy: dict) -> set[str]:
    if value == "company_domains":
        value = policy["company_domains"]
    if not isinstance(value, list) or not all(isinstance(d, str) for d in value):
        raise ValueError(f"invalid domain list: {value!r}")
    return {d.strip().lower() for d in value}


def _holds(op: str, value, facts: dict, policy: dict, daily_total_usd: float) -> bool:
    amount, recipients = facts["amount_usd"], facts["recipients"]
    if op == "amount_usd_lte":
        return amount is not None and amount <= _number(value)
    if op == "daily_total_usd_lte":
        return amount is not None and daily_total_usd + amount <= _number(value)
    domains = _domains(value, policy)
    if not recipients:  # an empty recipient list never matches
        return False
    check = all if op == "all_recipients_in" else any
    return check(_domain(r) in domains for r in recipients)


def _matches(rule: dict, facts: dict, policy: dict, daily_total_usd: float) -> bool:
    when = rule.get("when", {})
    unknown = set(when) - OPERATORS  # check all operators first, so order in `when` can't hide a typo
    if unknown:
        raise ValueError(f"unsupported operator in rule {rule.get('id')}: {sorted(unknown)}")
    return all(_holds(op, v, facts, policy, daily_total_usd) for op, v in when.items())


def _decide(effect: str, rule_id: str | None, reason: str, risk: str) -> Decision:
    if effect not in EFFECTS:
        raise ValueError(f"invalid effect: {effect!r}")
    if risk == "destructive":
        reason = f"[DESTRUCTIVE] {reason}"
    return Decision(effect, rule_id, reason, f"{AGENT_MESSAGES[effect]} {reason}")


def _evaluate(tool_name, args, policy, registry, daily_total_usd) -> Decision:
    try:
        facts = normalize(tool_name, args, registry)
    except ValueError as e:
        return Decision("deny", None, f"Invalid arguments: {e}",
                        f"The arguments could not be checked ({e}). " + AGENT_MESSAGES["deny"])
    for rule in policy["rules"]:
        if rule["category"] == facts["category"] and _matches(rule, facts, policy, daily_total_usd):
            reason = rule.get("explain") or f"Matched rule {rule['id']}"
            return _decide(rule["effect"], rule["id"], reason, facts["risk"])
    risk = facts["risk"]
    reason = (f"No rule matched; default for risk '{risk}'" if facts["registered"]
              else "Tool not in registry; treated as write")
    return _decide(policy["default_by_risk"][risk], None, reason, risk)


def evaluate(tool_name: str, args: dict, policy: dict, registry: dict,
             daily_total_usd: float) -> Decision:
    """Deterministic and pure. Any exception results in deny."""
    try:
        return _evaluate(tool_name, args, policy, registry, float(daily_total_usd))
    except Exception as e:
        return Decision("deny", None, f"Check failed: {type(e).__name__}: {e}",
                        "Policy check failed. " + AGENT_MESSAGES["deny"])
