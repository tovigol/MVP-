"""Deterministic rules built straight from the structured answers (no LLM).

Used as the fallback when Claude is unavailable or returns something invalid twice, so the
owner's amounts and domains are never lost. The free-text answer is not understood here.

Note: README section 5.1's example policy has no rule that denies large payments, but section 8
says "$5,000 is blocked". Without `pay-block`, a $5,000 payment would fall to the default for
risk `write` = ask. So we always emit it.
"""
from __future__ import annotations

from .policy_schema import COMPANY_DOMAINS, DEFAULT_BY_RISK


def usd(value: float) -> str:
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def baseline_rules(answers: dict) -> list[dict]:
    forbidden = set(answers.get("forbidden") or [])
    rules: list[dict] = []

    if "all_payments" in forbidden:
        rules.append({"id": "pay-block", "category": "payment", "effect": "deny", "when": {},
                      "explain": "כל תשלום חסום"})
    else:
        auto, daily, ceiling = (answers["auto_pay_limit_usd"], answers["daily_pay_limit_usd"],
                                answers["max_pay_limit_usd"])
        rules += [
            {"id": "pay-small", "category": "payment", "effect": "allow",
             "when": {"amount_usd_lte": auto, "daily_total_usd_lte": daily},
             "explain": f"תשלומים עד {usd(auto)}, ועד {usd(daily)} ביום, מתבצעים אוטומטית"},
            {"id": "pay-medium", "category": "payment", "effect": "ask",
             "when": {"amount_usd_lte": ceiling},
             "explain": f"תשלומים עד {usd(ceiling)} דורשים את אישורך"},
            {"id": "pay-block", "category": "payment", "effect": "deny", "when": {},
             "explain": f"תשלומים מעל {usd(ceiling)} חסומים"},
        ]

    if answers.get("company_domains"):
        rules.append({"id": "email-internal", "category": "email_send", "effect": "allow",
                      "when": {"all_recipients_in": COMPANY_DOMAINS},
                      "explain": "מיילים לכתובות של העסק נשלחים אוטומטית"})
    if answers.get("trusted_domains"):
        rules.append({"id": "email-allowlist", "category": "email_send", "effect": "allow",
                      "when": {"all_recipients_in": list(answers["trusted_domains"])},
                      "explain": f"מיילים לספקים הקבועים ({', '.join(answers['trusted_domains'])}) נשלחים אוטומטית"})
    if "external_email" in forbidden:
        rules.append({"id": "email-external", "category": "email_send", "effect": "deny", "when": {},
                      "explain": "מיילים לכתובות חיצוניות חסומים"})
    else:
        rules.append({"id": "email-external", "category": "email_send", "effect": "ask", "when": {},
                      "explain": "מיילים לכתובות חיצוניות דורשים את אישורך"})

    rules.append({"id": "read-all", "category": "read", "effect": "allow", "when": {},
                  "explain": "קריאת מידע מותרת"})
    if "bulk_export" in forbidden:
        rules.append({"id": "no-export", "category": "bulk_export", "effect": "deny", "when": {},
                      "explain": "ייצוא מלא של נתונים חסום"})
    return rules


def build_policy(answers: dict, rules: list[dict]) -> dict:
    """Assembles policy.json. Everything except `rules` comes from code, not from the model."""
    return {
        "business": answers["business_name"],
        "company_domains": list(answers["company_domains"]),
        "default_by_risk": dict(DEFAULT_BY_RISK),
        "rules": rules,
    }
