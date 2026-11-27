"""The onboarding questions (Hebrew UI) and cleaning of the owner's answers.

Numeric answers are structured on purpose: amounts and domains are the parts of a policy
where a typo is dangerous, so they are parsed by code and only handed to Claude as data.
The free-text answer is where Claude's translation adds value.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Literal

# Value of the "forbidden" multiselect -> Hebrew label.
FORBIDDEN_OPTIONS = {
    "bulk_export": "ייצוא מלא של רשימת הלקוחות או של נתונים אחרים",
    "external_email": "שליחת מייל לכתובות מחוץ לעסק (בלי לשאול אותי בכלל)",
    "all_payments": "כל תשלום, בכל סכום",
}

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class Question:
    id: str
    label: str
    kind: Literal["text", "number", "domains", "multiselect", "longtext"]
    help: str = ""
    default: object = ""
    placeholder: str = ""
    options: dict = field(default_factory=dict)  # multiselect only: value -> Hebrew label


QUESTIONS: list[Question] = [
    Question("business_name", "איך קוראים לעסק שלך?", "text",
             default="", placeholder="למשל: המאפייה של יוסי"),
    Question("company_domains", "מה הדומיין של כתובות המייל של העסק?", "domains",
             help="מיילים לכתובות בדומיין הזה נחשבים פנימיים. אפשר כמה דומיינים, מופרדים בפסיק.",
             placeholder="yossibakery.com"),
    Question("auto_pay_limit_usd", "עד איזה סכום (בדולרים) הסוכן רשאי לשלם לבד, בלי לשאול אותך?", "number",
             help="תשלום עד הסכום הזה יתבצע אוטומטית.", default=100.0),
    Question("daily_pay_limit_usd", "מה הסכום המצטבר המקסימלי ליום שהסוכן רשאי לשלם לבד?", "number",
             help="מונע פיצול של תשלום גדול לכמה תשלומים קטנים. מעבר לסכום הזה הסוכן יבקש אישור.",
             default=300.0),
    Question("max_pay_limit_usd", "מעל איזה סכום תשלום חסום לגמרי?", "number",
             help="בין הסכום האוטומטי לסכום הזה תקבל בקשת אישור. מעליו התשלום יידחה.",
             default=1000.0),
    Question("trusted_domains", "לאילו ספקים או לקוחות קבועים מותר לשלוח מייל בלי לשאול אותך?", "domains",
             help="דומיינים של גורמים חיצוניים שאתה סומך עליהם. אפשר להשאיר ריק.",
             placeholder="flour-supplier.com"),
    Question("forbidden", "מה אסור לסוכן לעשות בשום מקרה?", "multiselect",
             help="כל מה שלא סימנת ולא מוגדר במפורש יגיע אליך לאישור.",
             default=["bulk_export"], options=FORBIDDEN_OPTIONS),
    Question("free_text", "יש עוד משהו שחשוב לך? כתוב במשפט חופשי.", "longtext",
             help="Claude יתרגם את זה לחוקים. מה שאי אפשר לבטא כחוק יסומן לך בנפרד.",
             placeholder="למשל: אל תשלם לאף אחד יותר ממאה דולר בלי לשאול אותי."),
]


def parse_domains(text: str) -> tuple[list[str], list[str]]:
    """Splits free text into (valid lowercase domains, invalid tokens). Accepts 'a@b.com' and 'https://b.com'."""
    valid: list[str] = []
    invalid: list[str] = []
    for token in re.split(r"[,\s;]+", text or ""):
        token = token.strip()
        if not token:
            continue
        domain = re.sub(r"^[a-z]+://", "", token.lower())
        domain = domain.rpartition("@")[2].strip("/ .")
        if domain.startswith("www."):
            domain = domain[4:]
        if _DOMAIN_RE.match(domain):
            if domain not in valid:
                valid.append(domain)
        else:
            invalid.append(token)
    return valid, invalid


def _positive_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def clean_answers(raw: dict) -> tuple[dict, list[str]]:
    """Returns (clean answers, problems). `problems` are Hebrew messages for the owner; empty = OK."""
    problems: list[str] = []
    answers: dict = {}

    answers["business_name"] = str(raw.get("business_name") or "").strip()
    if not answers["business_name"]:
        problems.append("נא למלא את שם העסק.")

    for key, label, required in (("company_domains", "דומיין המייל של העסק", True),
                                 ("trusted_domains", "הספקים הקבועים", False)):
        domains, invalid = parse_domains(raw.get(key) or "")
        answers[key] = domains
        if invalid:
            problems.append(f"{label}: לא הצלחתי לקרוא את {', '.join(invalid)}. כתוב דומיין כמו yossibakery.com.")
        if required and not domains and not invalid:
            problems.append(f"נא למלא את {label}.")

    for key, label in (("auto_pay_limit_usd", "הסכום האוטומטי"),
                       ("daily_pay_limit_usd", "התקרה היומית"),
                       ("max_pay_limit_usd", "סכום החסימה")):
        number = _positive_number(raw.get(key))
        if number is None:
            problems.append(f"{label}: נא להזין מספר גדול מאפס.")
        answers[key] = number

    auto, daily, ceiling = (answers["auto_pay_limit_usd"], answers["daily_pay_limit_usd"],
                            answers["max_pay_limit_usd"])
    if auto and daily and daily < auto:
        problems.append("התקרה היומית צריכה להיות לפחות כמו הסכום האוטומטי לתשלום בודד.")
    if auto and ceiling and ceiling < auto:
        problems.append("סכום החסימה צריך להיות לפחות כמו הסכום האוטומטי.")

    forbidden = [v for v in (raw.get("forbidden") or []) if v in FORBIDDEN_OPTIONS]
    answers["forbidden"] = forbidden
    answers["free_text"] = str(raw.get("free_text") or "").strip()
    return answers, problems
