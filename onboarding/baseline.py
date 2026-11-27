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
    return f"{value:,.0f} ₪" if float(value).is_integer() else f"{value:,.2f} ₪"


def ils_per_usd() -> float:
    load_env()
    try:
        rate = float(os.environ.get("AGENTGUARD_ILS_PER_USD", DEFAULT_ILS_PER_USD))
    except ValueError:
        return DEFAULT_ILS_PER_USD
    return rate if math.isfinite(rate) and rate > 0 else DEFAULT_ILS_PER_USD


def limits(answers: dict | None = None) -> dict:
    """The two limits in shekels and in USD (rounded down, at least 1).

    The amounts are the ones the owner chose in questions 1 and 2; without answers, or for a question that was
    not ticked, the suggested amounts are used (the sample calls in equivalence.py still need both).
    """
    rate = ils_per_usd()
    auto = (answers or {}).get("auto_limit_ils") or AUTO_PAY_LIMIT_ILS
    daily = (answers or {}).get("daily_limit_ils") or DAILY_LIMIT_ILS
    return {"rate": rate,
            "auto_ils": auto, "auto_usd": max(1, math.floor(auto / rate)),
            "daily_ils": daily, "daily_usd": max(1, math.floor(daily / rate))}


def _rule(rule_id: str, category: str, effect: str, when: dict, explain: str) -> dict:
    return {"id": rule_id, "category": category, "effect": effect, "when": when, "explain": explain}


def baseline_rules(answers: dict) -> list[dict]:
    lim = limits(answers)
    auto, daily = answers["pay_auto"], answers["pay_daily_limit"]
    auto_txt = f"{ils(lim['auto_ils'])} (כ־${lim['auto_usd']:,})"
    daily_txt = f"{ils(lim['daily_ils'])} (כ־${lim['daily_usd']:,})"
    rules: list[dict] = []

    # --- payments ---
    if auto:
        when = {"amount_usd_lte": lim["auto_usd"]}
        text = f"תשלום עד {auto_txt} מתבצע אוטומטית"
        if daily:
            when["daily_total_usd_lte"] = lim["daily_usd"]
            text += f", כל עוד סך התשלומים היום לא עובר {daily_txt}"
        rules.append(_rule("pay-auto", "payment", "allow", when, text))
    if daily:
        rules.append(_rule("pay-ask", "payment", "ask", {"daily_total_usd_lte": lim["daily_usd"]},
                           f"תשלום אחר, שלא חורג מ־{daily_txt} ליום, דורש את אישורך"))
        rules.append(_rule("pay-daily-block", "payment", "deny", {},
                           f"תשלום שיחרוג מ־{daily_txt} ביום חסום"))
    else:
        rules.append(_rule("pay-ask", "payment", "ask", {},
                           f"תשלום מעל {auto_txt} דורש את אישורך" if auto else "כל תשלום דורש את אישורך"))

    # --- email ---
    if answers["mail_internal"]:
        rules.append(_rule("email-internal", "email_send", "allow", {"all_recipients_in": COMPANY_DOMAINS},
                           f"מיילים לכתובות במשרד ({', '.join(answers['company_domains'])}) נשלחים אוטומטית"))
    if answers["trusted_domains"]:
        rules.append(_rule("email-trusted", "email_send", "allow",
                           {"all_recipients_in": list(answers["trusted_domains"])},
                           f"מיילים לדומיינים הקבועים ({', '.join(answers['trusted_domains'])}) נשלחים אוטומטית"))
    if answers["mail_unknown_asks"]:
        rules.append(_rule("email-unknown", "email_send", "ask", {}, "מיילים לכתובות לא מוכרות דורשים את אישורך"))
    else:
        rules.append(_rule("email-unknown", "email_send", "deny", {}, "מיילים לכתובות לא מוכרות חסומים"))

    # --- read / export ---
    if answers["read_allowed"]:
        rules.append(_rule("read-all", "read", "allow", {}, "קריאת מידע (חשבוניות, פרטי לקוחות) מותרת"))
    else:
        rules.append(_rule("read-ask", "read", "ask", {}, "קריאת מידע דורשת את אישורך"))
    if answers["block_export"]:
        rules.append(_rule("no-export", "bulk_export", "deny", {}, "ייצוא מלא של רשימת הלקוחות חסום"))
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
    """What the owner asked for that the Guard cannot enforce (or enforces more strictly). Hebrew, for the checklist."""
    lim = limits(answers)
    notes: list[str] = []
    if answers["pay_trusted_suppliers"]:
        notes.append(f"שאלה 3: הספקים הקבועים ({', '.join(answers['supplier_names'])}) נשמרו, אבל ה־Guard עדיין "
                     "לא מזהה למי משולם התשלום ולכן לא יכול להתיר תשלום לפי ספק. תשלומים נבדקים לפי הסכום בלבד.")
    if answers["pay_auto"] and answers["pay_new_supplier_asks"]:
        notes.append(f"שאלה 4: ה־Guard לא יכול להבדיל בין ספק חדש לספק קבוע, ולכן תשלום עד {ils(lim['auto_ils'])} "
                     "יתבצע בלי אישור גם אם הוא לספק חדש. כדי לקבל אישור לכל ספק חדש, להשאיר את שאלה 1 לא מסומנת.")
    if answers["mail_files"]:
        notes.append("שאלה 8: ה־Guard לא בודק קבצים מצורפים, ולכן אי אפשר להתיר שליחת קובץ בלי אישור. "
                     "מיילים ללקוחות נבדקים לפי הכתובת בלבד.")
    if not answers["mail_unknown_asks"]:
        notes.append("שאלה 7: מייל לכתובת לא מוכרת לא יישלח לאישור אלא ייחסם, כי ה־Guard אינו מתיר שליחה "
                     "חופשית לכל כתובת.")
    if not answers["block_export"]:
        notes.append("שאלה 9: ייצוא רשימת הלקוחות לא נחסם תמיד, אבל הוא ידרוש את אישורך בכל פעם.")
    return notes + list(answers.get("warnings", []))
