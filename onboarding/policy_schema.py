"""Contract for what Onboarding may write to policy.json: JSON schema for Claude + validation in code.

The engine (shared/policy.py) is deterministic and fail-closed, so a rule it cannot use is not
"ignored": it silently never matches, or turns into a deny. This module catches those cases
before the file is written. Constants come from shared/policy.py so the two cannot drift apart.
"""
from __future__ import annotations

import math
import re

from shared.policy import EFFECTS, OPERATORS, RISKS

CATEGORIES = ("payment", "email_send", "read", "bulk_export")
AMOUNT_OPERATORS = frozenset({"amount_usd_lte", "daily_total_usd_lte"})
RECIPIENT_OPERATORS = frozenset({"all_recipients_in", "any_recipient_in"})
COMPANY_DOMAINS = "company_domains"  # keyword: "the business's own domains"
DEFAULT_BY_RISK = {"read": "allow", "write": "ask", "destructive": "ask"}

_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")

# Structured-output schema for Claude's answer. Only the rules come from the model; the rest
# of policy.json (business, company_domains, default_by_risk) is built by code.
RULES_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "effect": {"type": "string", "enum": list(EFFECTS)},
                    "when": {
                        "type": "object",
                        "properties": {
                            "amount_usd_lte": {"type": "number"},
                            "daily_total_usd_lte": {"type": "number"},
                            "all_recipients_in": {"type": "array", "items": {"type": "string"}},
                            "any_recipient_in": {"type": "array", "items": {"type": "string"}},
                        },
                        "additionalProperties": False,
                    },
                    "explain": {"type": "string"},
                },
                "required": ["id", "category", "effect", "when", "explain"],
                "additionalProperties": False,
            },
        },
        "unsupported": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["rules", "unsupported"],
    "additionalProperties": False,
}


def _is_positive_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _is_domain_list(value) -> bool:
    return (isinstance(value, list) and len(value) > 0
            and all(isinstance(d, str) and _DOMAIN_RE.match(d) for d in value))


def normalize_rules(rules: list) -> list:
    """Cleans Claude's rules: ['company_domains'] -> 'company_domains'; domains lowercased and stripped."""
    out = []
    for rule in rules:
        rule = dict(rule)
        when = dict(rule.get("when") or {})
        for op in RECIPIENT_OPERATORS:
            value = when.get(op)
            if isinstance(value, list):
                value = [d.strip().lower() for d in value if isinstance(d, str)]
                when[op] = COMPANY_DOMAINS if value == [COMPANY_DOMAINS] else value
        rule["when"] = when
        out.append(rule)
    return out


def _validate_when(when, category, company_domains, label, errors) -> bool:
    if not isinstance(when, dict):
        errors.append(f"{label}: 'when' must be an object")
        return False
    ok = True
    allowed = (AMOUNT_OPERATORS if category == "payment"
               else RECIPIENT_OPERATORS if category == "email_send" else frozenset())
    for op, value in when.items():
        if op not in OPERATORS:
            errors.append(f"{label}: unsupported operator '{op}' (allowed: {sorted(OPERATORS)})")
            ok = False
        elif op not in allowed:
            errors.append(f"{label}: operator '{op}' never matches category '{category}'")
            ok = False
        elif op in AMOUNT_OPERATORS:
            if not _is_positive_number(value):
                errors.append(f"{label}: '{op}' must be a positive number, got {value!r}")
                ok = False
        elif value == COMPANY_DOMAINS:
            if not company_domains:
                errors.append(f"{label}: uses '{COMPANY_DOMAINS}' but the policy has no company_domains")
                ok = False
        elif not _is_domain_list(value):
            errors.append(f"{label}: '{op}' must be a non-empty list of lowercase domains or '{COMPANY_DOMAINS}'")
            ok = False
    return ok


def _validate_safety(rule, label, errors) -> bool:
    """Hard safety rules: an `allow` must always be bounded."""
    if rule["effect"] != "allow":
        return True
    when, category = rule["when"], rule["category"]
    if category == "payment" and "amount_usd_lte" not in when:
        errors.append(f"{label}: an allow rule for payments must have amount_usd_lte")
        return False
    if category == "email_send" and "all_recipients_in" not in when:
        errors.append(f"{label}: an allow rule for email_send must have all_recipients_in "
                      "(any_recipient_in lets a hidden external recipient through)")
        return False
    if category == "bulk_export":
        errors.append(f"{label}: bulk_export must never be allowed")
        return False
    return True


def _implies(narrow: dict, broad: dict) -> bool:
    """True if every call matching `narrow` also matches `broad` (so `broad` listed first shadows `narrow`)."""
    for op, broad_value in broad.items():
        if op not in narrow:
            return False
        narrow_value = narrow[op]
        if op in AMOUNT_OPERATORS:
            if narrow_value > broad_value:
                return False
        elif isinstance(narrow_value, str) or isinstance(broad_value, str):
            if narrow_value != broad_value:
                return False
        elif not set(narrow_value) <= set(broad_value):
            return False
    return True


def validate_policy(policy) -> list[str]:
    """Returns a list of problems (English, fed back to Claude on retry). Empty list = valid."""
    if not isinstance(policy, dict):
        return ["policy must be an object"]
    errors: list[str] = []

    if not isinstance(policy.get("business"), str):
        errors.append("business must be a string")
    company_domains = policy.get("company_domains")
    if not (isinstance(company_domains, list)
            and all(isinstance(d, str) and _DOMAIN_RE.match(d) for d in company_domains)):
        errors.append("company_domains must be a list of lowercase domains")
        company_domains = []
    default_by_risk = policy.get("default_by_risk")
    if not (isinstance(default_by_risk, dict) and set(default_by_risk) == set(RISKS)
            and all(v in EFFECTS for v in default_by_risk.values())):
        errors.append(f"default_by_risk must map exactly {list(RISKS)} to one of {list(EFFECTS)}")

    rules = policy.get("rules")
    if not isinstance(rules, list) or not rules:
        errors.append("rules must be a non-empty list")
        return errors

    seen_ids: set[str] = set()
    valid_rules: list[tuple[str, dict]] = []
    for i, rule in enumerate(rules, start=1):
        if not isinstance(rule, dict):
            errors.append(f"rule #{i}: must be an object")
            continue
        rule_id = rule.get("id")
        label = f"rule '{rule_id}'" if isinstance(rule_id, str) and rule_id else f"rule #{i}"
        ok = True
        if not (isinstance(rule_id, str) and _ID_RE.match(rule_id)):
            errors.append(f"{label}: id must be lowercase kebab-case (letters, digits, hyphens)")
            ok = False
        elif rule_id in seen_ids:
            errors.append(f"{label}: duplicate id")
            ok = False
        else:
            seen_ids.add(rule_id)
        if rule.get("category") not in CATEGORIES:
            errors.append(f"{label}: category must be one of {list(CATEGORIES)}")
            ok = False
        if rule.get("effect") not in EFFECTS:
            errors.append(f"{label}: effect must be one of {list(EFFECTS)}")
            ok = False
        if not (isinstance(rule.get("explain"), str) and rule["explain"].strip()):
            errors.append(f"{label}: explain must be a non-empty string")
            ok = False
        if ok:
            ok = _validate_when(rule.get("when"), rule["category"], company_domains, label, errors)
        if ok:
            ok = _validate_safety(rule, label, errors)
        if ok:
            valid_rules.append((rule["id"], rule))

    # First match wins: a rule is dead if an earlier rule in the same category already covers it.
    for j, (rule_id, rule) in enumerate(valid_rules):
        for earlier_id, earlier in valid_rules[:j]:
            if earlier["category"] == rule["category"] and _implies(rule["when"], earlier["when"]):
                errors.append(f"rule '{rule_id}' can never match: earlier rule '{earlier_id}' already covers it "
                              "(order rules from specific to general, catch-all last)")
                break
    return errors
