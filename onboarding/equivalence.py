"""Checks that a policy behaves the way the owner's answers require, using the real rules engine.

The model writes the rules, but what the owner answered is not up for interpretation. The reference
(baseline.py) is built from the answers by code; this module replays a set of sample calls through
shared.policy.evaluate against both and reports every call where they differ. A mismatch is sent back
to the model as feedback, and after a failed retry the reference itself is used.
"""
from __future__ import annotations

import re

from shared.policy import evaluate

from .baseline import baseline_rules, build_policy, limits

# A stand-in registry: one tool of each kind the questionnaire talks about.
REGISTRY = {
    "pay": {"category": "payment", "risk": "write", "amount_field": "amount", "amount_unit": "usd",
            "source": "reviewed"},
    "mail": {"category": "email_send", "risk": "write", "recipient_fields": ["to", "cc", "bcc"],
             "source": "reviewed"},
    "read": {"category": "read", "risk": "read", "source": "reviewed"},
    "export": {"category": "bulk_export", "risk": "write", "source": "reviewed"},
    "delete": {"category": "other", "risk": "destructive", "source": "reviewed"},
}

_UNKNOWN_ADDRESS = "someone@unknown-example.org"
MAX_REPORTED = 8


def reference_policy(answers: dict) -> dict:
    return build_policy(answers, baseline_rules(answers))


def _calls(answers: dict):
    """(description, tool, args, today's total so far) for the sample calls."""
    lim = limits(answers)
    auto, daily = lim["auto_usd"], lim["daily_usd"]
    calls = []
    for amount in sorted({1, auto, auto + 1, daily, daily + 1, 10 * daily}):
        for total in sorted({0, auto, daily - 1, daily}):
            calls.append((f"payment of ${amount} when ${total} was already paid today", "pay",
                          {"amount": amount}, total))

    domains = list(answers["company_domains"]) + list(answers["trusted_domains"])
    addresses = [f"person@{d}" for d in domains] + [_UNKNOWN_ADDRESS]
    if domains:
        addresses += [f"PERSON@{domains[0].upper()}", f"person@{domains[0]}.evil.io"]
    for address in addresses:
        calls.append((f"email to {address}", "mail", {"to": address}, 0))
    if domains:
        calls.append((f"email to person@{domains[0]} with a hidden bcc to {_UNKNOWN_ADDRESS}", "mail",
                      {"to": f"person@{domains[0]}", "bcc": _UNKNOWN_ADDRESS}, 0))

    calls += [("a read tool", "read", {}, 0), ("an export tool", "export", {}, 0),
              ("a delete tool", "delete", {}, 0), ("a tool that is not in the registry", "mystery", {}, 0)]
    return calls


# ₪2,000 / 2,000 ₪ / $540
_AMOUNT = re.compile(r"₪\s*(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*₪|\$\s*(\d[\d,]*(?:\.\d+)?)")
MAX_EXPLAIN_CHARS = 200


def _amounts(text: str) -> set[str]:
    return {"".join(groups).replace(",", "") for groups in _AMOUNT.findall(text)}


def explain_problems(policy: dict, answers: dict | None = None) -> list[str]:
    """The owner reads every `explain` and the agent receives it, so the facts in it must be true.

    Decisions are checked by semantic_problems; here: only payment rules may mention amounts, and only the
    amounts that rule is about (those in the reference explanation of the same rule id, or else either
    limit, in shekels or USD); the text must be short English. Grammar and wording cannot be checked by code.
    """
    lim = limits(answers)
    both = {str(lim[k]) for k in ("auto_ils", "auto_usd", "daily_ils", "daily_usd")}
    reference = ({r["id"]: _amounts(r["explain"]) for r in reference_policy(answers)["rules"]}
                 if answers else {})
    problems = []
    for rule in policy["rules"]:
        text, rule_id = rule["explain"], rule["id"]
        allowed = reference.get(rule_id) or both
        if rule["category"] != "payment" and ("₪" in text or "$" in text):
            problems.append(f"rule '{rule_id}': explain must not mention amounts or limits; "
                            "only payment rules do")
        elif rule["category"] == "payment" and not _amounts(text) <= allowed:
            problems.append(f"rule '{rule_id}': explain mentions an amount this rule is not about; "
                            f"it may mention only {', '.join(sorted(allowed, key=int))}")
        elif rule["category"] == "payment" and "$" in text and "₪" not in text:
            problems.append(f"rule '{rule_id}': write amounts in shekels (₪), the owner's currency; a dollar "
                            "amount may only follow the shekel amount, in parentheses")
        if re.search("[א-ת]", text) or not re.search("[A-Za-z]", text):
            problems.append(f"rule '{rule_id}': explain must be in English")
        if len(text) > MAX_EXPLAIN_CHARS:
            problems.append(f"rule '{rule_id}': explain must be one short sentence")
    return problems


def semantic_problems(answers: dict, policy: dict) -> list[str]:
    """Calls where `policy` does not do what the owner's answers require (empty list = it does)."""
    reference = reference_policy(answers)
    problems = []
    for description, tool, args, total in _calls(answers):
        want = evaluate(tool, args, reference, REGISTRY, total).effect
        got = evaluate(tool, args, policy, REGISTRY, total).effect
        if got != want:
            problems.append(f"{description}: your rules give '{got}' but the owner's answers require '{want}'")
    if len(problems) > MAX_REPORTED:
        problems = problems[:MAX_REPORTED] + [f"... and {len(problems) - MAX_REPORTED} more calls differ"]
    return problems
