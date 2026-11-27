"""The onboarding questionnaire (Hebrew UI): 11 yes/no questions in three parts, and cleaning of the answers.

Every question is a checkbox. Its starting state is the "if not answered" default of the questionnaire, which is
always the cautious answer, so a form left untouched produces a cautious policy.

Three questions ask for a follow-up when ticked (3: supplier names, 5: the office's mail domain, 6: the fixed
addresses). Question 5's domain is not in the spec text: without it "mail inside the office" cannot be matched.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Literal

AUTO_PAY_LIMIT_ILS = 2000   # question 1: the suggested amount (the owner can choose another)
DAILY_LIMIT_ILS = 7000      # question 2: the suggested amount (the owner can choose another)
MAX_AMOUNT_ILS = 10_000_000  # sanity bound for an amount typed by hand

# Allowing a free-mail domain would allow every address at that provider, so such domains are never
# turned into an allow rule (the engine compares whole domains, not single addresses).
FREE_MAIL_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com",
    "ymail.com", "icloud.com", "me.com", "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com",
    "yandex.com", "walla.co.il", "walla.com", "012.net.il", "013.net", "013net.net", "netvision.net.il",
    "bezeqint.net", "zahav.net.il",
})

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


@dataclass(frozen=True)
class FollowUp:
    id: str
    label: str
    placeholder: str
    kind: str = "text"   # "text", or "amount": a number of shekels the owner chooses
    default: int = 0     # the suggested amount (kind "amount")


@dataclass(frozen=True)
class Question:
    id: str
    number: int
    section: str
    text: str
    default: bool        # the state when the owner does not answer
    default_note: str    # what that default means, in Hebrew
    follow_up: FollowUp | None = None


# Section titles and default notes are Streamlit markdown: ":material/name:" is a Material Symbols icon.
SECTIONS = {"payments": ":material/payments: חלק א: תשלומים",
            "mail": ":material/mail: חלק ב: מיילים",
            "data": ":material/folder_open: חלק ג: מידע"}

QUESTIONS: list[Question] = [
    Question("pay_auto", 1, "payments",
             "האם הסוכן יכול לשלם בלי לשאול אותך, עד סכום שתבחרי?",
             False, ":red[:material/close:] לא, כל תשלום מגיע לאישור",
             FollowUp("auto_limit_ils", "עד איזה סכום, בשקלים, הסוכן ישלם בלי לשאול?", "",
                      kind="amount", default=AUTO_PAY_LIMIT_ILS)),
    Question("pay_daily_limit", 2, "payments",
             "האם להגביל את סך התשלומים ביום?",
             True, f":green[:material/check:] כן, יש הגבלה (בברירת מחדל {DAILY_LIMIT_ILS:,} ₪)",
             FollowUp("daily_limit_ils", "מה הסכום היומי המקסימלי, בשקלים?", "",
                      kind="amount", default=DAILY_LIMIT_ILS)),
    Question("pay_trusted_suppliers", 3, "payments",
             "האם הסוכן יכול לשלם לספקים הקבועים שלך בלי לשאול?",
             False, ":red[:material/close:] לא",
             FollowUp("supplier_names", "שמות הספקים הקבועים", "למשל: חברת החשמל, ספק הקמח")),
    Question("pay_new_supplier_asks", 4, "payments",
             "האם תשלום לספק חדש יגיע אלייך לאישור, גם בסכום קטן?",
             True, ":green[:material/check:] כן, תמיד לאישור"),
    Question("mail_internal", 5, "mail",
             "האם הסוכן יכול לשלוח מייל לתוך המשרד שלך בלי לשאול?",
             False, ":red[:material/close:] לא",
             FollowUp("internal_domain", "הדומיין של המשרד", "office.co.il")),
    Question("mail_trusted", 6, "mail",
             "האם הסוכן יכול לשלוח מייל לכתובות קבועות, כמו רשות המסים ורואה החשבון, בלי לשאול?",
             False, ":red[:material/close:] לא",
             FollowUp("trusted_addresses", "הכתובות הקבועות", "accountant@cpa-firm.co.il, office@taxes.gov.il")),
    Question("mail_unknown_asks", 7, "mail",
             "האם מייל לכתובת לא מוכרת יגיע אלייך לאישור?",
             True, ":green[:material/check:] כן"),
    Question("mail_files", 8, "mail",
             "האם הסוכן יכול לשלוח ללקוח קובץ, כמו דוח או תלוש, בלי שתראי קודם?",
             False, ":red[:material/close:] לא"),
    Question("block_export", 9, "data",
             "האם לחסום תמיד ייצוא של כל רשימת הלקוחות?",
             True, ":green[:material/check:] כן, חסום"),
    Question("block_delete", 10, "data",
             "האם לחסום תמיד מחיקה של חשבוניות או מסמכים?",
             True, ":green[:material/check:] כן, חסום"),
    Question("read_allowed", 11, "data",
             "האם הסוכן יכול לקרוא מידע, כמו חשבוניות ופרטי לקוחות, בלי לשאול?",
             True, ":green[:material/check:] כן, קריאה בלבד מותרת"),
]

QUESTION_BY_ID = {q.id: q for q in QUESTIONS}


def _tokens(text) -> list[str]:
    return [t.strip() for t in re.split(r"[,\s;]+", str(text or "")) if t.strip()]


def _domain_of(token: str) -> str:
    domain = re.sub(r"^[a-z]+://", "", token.lower()).rpartition("@")[2].strip("/ .")
    return domain[4:] if domain.startswith("www.") else domain


def _amount(value, default: int):
    """A positive number of shekels (whole when it can be), the default when nothing was given, None if invalid."""
    if value is None or str(value).strip() == "":
        return default
    try:
        number = float(str(value).replace(",", "").replace("₪", "").strip())
    except ValueError:
        return None
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0 or number > MAX_AMOUNT_ILS:
        return None
    return int(number) if number == int(number) else round(number, 2)


def parse_domains(text) -> tuple[list[str], list[str]]:
    """(valid lowercase domains, invalid tokens). Accepts 'a@b.com' and 'https://b.com' too."""
    valid: list[str] = []
    invalid: list[str] = []
    for token in _tokens(text):
        domain = _domain_of(token)
        if _DOMAIN_RE.match(domain):
            if domain not in valid:
                valid.append(domain)
        else:
            invalid.append(token)
    return valid, invalid


def parse_addresses(text) -> tuple[list[tuple[str, str]], list[str]]:
    """([(what the owner typed, its domain)], invalid tokens). A bare domain is accepted as well as user@domain."""
    entries: list[tuple[str, str]] = []
    invalid: list[str] = []
    for token in _tokens(text):
        local, at, _ = token.rpartition("@")
        domain = _domain_of(token)
        if _DOMAIN_RE.match(domain) and (not at or local):
            entry = (token.lower(), domain)
            if entry not in entries:
                entries.append(entry)
        else:
            invalid.append(token)
    return entries, invalid


def clean_answers(raw: dict) -> tuple[dict, list[str]]:
    """Returns (clean answers, problems). `problems` are Hebrew messages for the owner; empty = OK.

    `raw` maps each question id to a bool (missing = the question's default) and each follow-up id to text.
    Cautious notes about what could not be taken literally go to answers["warnings"].
    """
    problems: list[str] = []
    warnings: list[str] = []
    answers: dict = {q.id: bool(raw.get(q.id, q.default)) for q in QUESTIONS}

    # 1 and 2: the amounts the owner chose (a missing value means the suggested amount)
    for question_id, number in (("pay_auto", 1), ("pay_daily_limit", 2)):
        follow_up = QUESTION_BY_ID[question_id].follow_up
        answers[follow_up.id] = None
        if answers[question_id]:
            amount = _amount(raw.get(follow_up.id), follow_up.default)
            if amount is None:
                problems.append(f"שאלה {number}: נא לבחור סכום בשקלים, גדול מאפס.")
            answers[follow_up.id] = amount
    auto, daily = answers["auto_limit_ils"], answers["daily_limit_ils"]
    if auto and daily and daily < auto:
        problems.append("שאלה 2: ההגבלה היומית צריכה להיות לפחות כמו הסכום שבחרת בשאלה 1.")

    # 3: supplier names (kept for the record; the engine cannot match a payee, see baseline.limitation_notes)
    names = [n.strip() for n in re.split(r"[,\n;]+", str(raw.get("supplier_names") or "")) if n.strip()]
    answers["supplier_names"] = names if answers["pay_trusted_suppliers"] else []
    if answers["pay_trusted_suppliers"] and not names:
        problems.append("שאלה 3: נא להקליד את שמות הספקים הקבועים.")

    # 5: the office's own mail domain
    answers["company_domains"] = []
    if answers["mail_internal"]:
        domains, invalid = parse_domains(raw.get("internal_domain"))
        free = [d for d in domains if d in FREE_MAIL_DOMAINS]
        domains = [d for d in domains if d not in FREE_MAIL_DOMAINS]
        if invalid:
            problems.append(f"שאלה 5: לא הצלחתי לקרוא את {', '.join(invalid)}. כתוב דומיין כמו office.co.il.")
        elif free:
            problems.append(f"שאלה 5: {', '.join(free)} הוא שירות מייל חינמי. היתר לדומיין כזה היה מתיר מייל לכל "
                            "הכתובות בו. יש להקליד את הדומיין של המשרד עצמו.")
        elif not domains:
            problems.append("שאלה 5: נא להקליד את הדומיין של המשרד, למשל office.co.il.")
        answers["company_domains"] = domains

    # 6: fixed addresses -> allowed by domain
    answers["trusted_addresses"], answers["trusted_domains"] = [], []
    if answers["mail_trusted"]:
        entries, invalid = parse_addresses(raw.get("trusted_addresses"))
        usable = [(a, d) for a, d in entries if d not in FREE_MAIL_DOMAINS]
        excluded = [a for a, d in entries if d in FREE_MAIL_DOMAINS]
        if invalid:
            problems.append(f"שאלה 6: לא הצלחתי לקרוא את {', '.join(invalid)}. כתוב כתובת כמו accountant@cpa-firm.co.il.")
        elif not entries:
            problems.append("שאלה 6: נא להקליד את הכתובות הקבועות.")
        elif not usable:
            problems.append("שאלה 6: כל הכתובות הן בשירות מייל חינמי (למשל gmail.com), ולא ניתן להתיר אותן "
                            "בלי להתיר את כל הכתובות בשירות. מיילים אליהן ימשיכו להגיע לאישור.")
        elif excluded:
            warnings.append(f"הכתובות {', '.join(excluded)} לא הותרו: הן בשירות מייל חינמי, וההיתר היה חל על "
                            "כל הכתובות בשירות. הן יטופלו כמו כל כתובת לא מוכרת (שאלה 7).")
        answers["trusted_addresses"] = [a for a, _ in usable]
        answers["trusted_domains"] = list(dict.fromkeys(d for _, d in usable))

    answers["warnings"] = warnings
    return answers, problems
