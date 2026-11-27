"""Deterministic policy built straight from the questionnaire answers (no LLM).

It is the fallback when the model is unavailable, and the reference the model's rules are checked against
(equivalence.py), so the owner's answers can never be mis-read by the model.

Mapping (rule order matters, first match wins):
  payments  1 pay_auto, 2 pay_daily_limit -> pay-auto (allow), pay-ask (ask), pay-daily-block (deny)
  email     5 mail_internal, 6 trusted domains, 7 mail_unknown_asks -> email-internal, email-trusted, email-unknown
  read      11 read_allowed -> read-all (allow) or read-ask (ask)
  export    9 block_export -> no-export (deny); otherwise the default for risk "write" (ask) applies
  delete    10 block_delete -> default_by_risk.destructive = deny (otherwise ask)

Not enforceable with the four operators of README 5.1, so they are reported to the owner (limitation_notes):
  3 and 4 (who is paid: no operator matches a payee) and 8 (attachments).

Amounts: the questionnaire is in shekels, the engine compares USD (README). Limits are converted with
AGENTGUARD_ILS_PER_USD (default 3.7, an assumption and not a live rate) and rounded DOWN, so a limit never
ends up higher than the owner asked for.
"""
from __future__ import annotations

import math
import os

from .llm import load_env
from .policy_schema import COMPANY_DOMAINS
from .questions import AUTO_PAY_LIMIT_ILS, DAILY_LIMIT_ILS

DEFAULT_ILS_PER_USD = 3.7


def ils(value: float) -> str:
    return f"₪{value:,.0f}"


def ils_per_usd() -> float:
    load_env()
    try:
        rate = float(os.environ.get("AGENTGUARD_ILS_PER_USD", DEFAULT_ILS_PER_USD))
    except ValueError:
        return DEFAULT_ILS_PER_USD
    return rate if math.isfinite(rate) and rate > 0 else DEFAULT_ILS_PER_USD


def limits(answers: dict | None = None) -> dict:
    """The two limits in shekels and in USD (rounded down, at least 1)."""
    rate = ils_per_usd()
    return {"rate": rate,
            "auto_ils": AUTO_PAY_LIMIT_ILS, "auto_usd": max(1, math.floor(AUTO_PAY_LIMIT_ILS / rate)),
            "daily_ils": DAILY_LIMIT_ILS, "daily_usd": max(1, math.floor(DAILY_LIMIT_ILS / rate))}


def _rule(rule_id: str, category: str, effect: str, when: dict, explain: str) -> dict:
    return {"id": rule_id, "category": category, "effect": effect, "when": when, "explain": explain}


def baseline_rules(answers: dict) -> list[dict]:
    lim = limits()
    auto, daily = answers["pay_auto"], answers["pay_daily_limit"]
    auto_txt = f"{ils(lim['auto_ils'])} (about ${lim['auto_usd']:,})"
    daily_txt = f"{ils(lim['daily_ils'])} (about ${lim['daily_usd']:,})"
    rules: list[dict] = []

    # --- payments ---
    if auto:
        when = {"amount_usd_lte": lim["auto_usd"]}
        text = f"Payments up to {auto_txt} go through automatically"
        if daily:
            when["daily_total_usd_lte"] = lim["daily_usd"]
            text += f", as long as today's total stays under {daily_txt}"
        rules.append(_rule("pay-auto", "payment", "allow", when, text))
    if daily:
        rules.append(_rule("pay-ask", "payment", "ask", {"daily_total_usd_lte": lim["daily_usd"]},
                           f"Other payments, within {daily_txt} a day, need your approval"))
        rules.append(_rule("pay-daily-block", "payment", "deny", {},
                           f"Payments that would go over {daily_txt} a day are blocked"))
    else:
        rules.append(_rule("pay-ask", "payment", "ask", {},
                           f"Payments over {auto_txt} need your approval" if auto else "Every payment needs your approval"))

    # --- email ---
    if answers["mail_internal"]:
        rules.append(_rule("email-internal", "email_send", "allow", {"all_recipients_in": COMPANY_DOMAINS},
                           f"Emails to office addresses ({', '.join(answers['company_domains'])}) are sent automatically"))
    if answers["trusted_domains"]:
        rules.append(_rule("email-trusted", "email_send", "allow",
                           {"all_recipients_in": list(answers["trusted_domains"])},
                           f"Emails to the fixed domains ({', '.join(answers['trusted_domains'])}) are sent automatically"))
    if answers["mail_unknown_asks"]:
        rules.append(_rule("email-unknown", "email_send", "ask", {}, "Emails to unknown addresses need your approval"))
    else:
        rules.append(_rule("email-unknown", "email_send", "deny", {}, "Emails to unknown addresses are blocked"))

    # --- read / export ---
    if answers["read_allowed"]:
        rules.append(_rule("read-all", "read", "allow", {}, "Reading information (invoices, customer details) is allowed"))
    else:
        rules.append(_rule("read-ask", "read", "ask", {}, "Reading information needs your approval"))
    if answers["block_export"]:
        rules.append(_rule("no-export", "bulk_export", "deny", {}, "Exporting the whole customer list is blocked"))
    return rules


def baseline_default_by_risk(answers: dict) -> dict:
    """Question 10 lives here: tools with risk "destructive" that no rule matches (deleting documents)."""
    return {"read": "allow" if answers["read_allowed"] else "ask",
            "write": "ask",
            "destructive": "deny" if answers["block_delete"] else "ask"}


def build_policy(answers: dict, rules: list[dict]) -> dict:
    """Assembles policy.json. Everything except `rules` comes from code, not from the model."""
    return {
        "business": "",
        "company_domains": list(answers["company_domains"]),
        "default_by_risk": baseline_default_by_risk(answers),
        "rules": rules,
    }


def limitation_notes(answers: dict) -> list[str]:
    """What the owner asked for that the Guard cannot enforce (or enforces more strictly). Plain English, for the checklist."""
    lim = limits()
    notes: list[str] = []
    if answers["pay_trusted_suppliers"]:
        notes.append(f"Question 3: your regular suppliers ({', '.join(answers['supplier_names'])}) were saved, but the Guard "
                     "can't yet tell who a payment goes to, so it can't allow payments by supplier. Payments are checked by amount only.")
    if answers["pay_auto"] and answers["pay_new_supplier_asks"]:
        notes.append(f"Question 4: the Guard can't tell a new supplier from a regular one, so a payment up to {ils(lim['auto_ils'])} "
                     "goes through without approval even to a new supplier. To approve every new supplier, leave question 1 off.")
    if answers["mail_files"]:
        notes.append("Question 8: the Guard doesn't check attachments, so sending a file can't be allowed without approval. "
                     "Emails to customers are checked by address only.")
    if not answers["mail_unknown_asks"]:
        notes.append("Question 7: an email to an unknown address will be blocked rather than sent for approval, because the "
                     "Guard never allows sending freely to any address.")
    if not answers["block_export"]:
        notes.append("Question 9: exporting the customer list isn't always blocked, but it will need your approval every time.")
    return notes + list(answers.get("warnings", []))
