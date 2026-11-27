"""The onboarding questionnaire (English UI): 11 yes/no questions in three parts, and cleaning of the answers.

Every question is a checkbox with its own icon. Its starting state is the "if not answered" default of the questionnaire, which is
always the cautious answer, so a form left untouched produces a cautious policy.

Five questions ask for a follow-up when ticked: 1 and 2 let the owner choose the amount (in shekels, with a
suggestion), 3 asks for supplier names, 5 for the office's mail domain, 6 for the fixed addresses. Question 5's
domain is not in the spec text: without it "mail inside the office" cannot be matched.
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
    default_note: str    # what that default means, for the owner
    follow_up: FollowUp | None = None
    icon: str = "help"   # Material Symbols name shown before the question (https://fonts.google.com/icons)


# Section titles and default notes are Streamlit markdown: ":material/name:" is a Material Symbols icon.
SECTIONS = {"payments": ":material/payments: Part 1: Payments",
            "mail": ":material/mail: Part 2: Emails",
            "data": ":material/folder_open: Part 3: Data"}

QUESTIONS: list[Question] = [
    Question("pay_auto", 1, "payments",
             "Can the agent pay without asking you, up to an amount you choose?",
             False, ":red[:material/close:] No, every payment needs your approval",
             FollowUp("auto_limit_ils", "Up to how much, in shekels (₪), can the agent pay without asking?", "",
                      kind="amount", default=AUTO_PAY_LIMIT_ILS),
             icon="bolt"),
    Question("pay_daily_limit", 2, "payments",
             "Limit the total payments per day?",
             True, f":green[:material/check:] Yes, there is a limit (₪{DAILY_LIMIT_ILS:,} by default)",
             FollowUp("daily_limit_ils", "What is the most the agent can pay in a day, in shekels (₪)?", "",
                      kind="amount", default=DAILY_LIMIT_ILS),
             icon="today"),
    Question("pay_trusted_suppliers", 3, "payments",
             "Can the agent pay your regular suppliers without asking?",
             False, ":red[:material/close:] No",
             FollowUp("supplier_names", "Names of your regular suppliers",
                      "For example: the electric company, the flour supplier"),
             icon="handshake"),
    Question("pay_new_supplier_asks", 4, "payments",
             "Should a payment to a new supplier come to you for approval, even a small one?",
             True, ":green[:material/check:] Yes, always for approval", icon="person_add"),
    Question("mail_internal", 5, "mail",
             "Can the agent email people inside your office without asking?",
             False, ":red[:material/close:] No",
             FollowUp("internal_domain", "Your office's email domain", "office.co.il"),
             icon="corporate_fare"),
    Question("mail_trusted", 6, "mail",
             "Can the agent email fixed addresses, such as the tax authority and your accountant, without asking?",
             False, ":red[:material/close:] No",
             FollowUp("trusted_addresses", "The fixed addresses", "accountant@cpa-firm.co.il, office@taxes.gov.il"),
             icon="contact_mail"),
    Question("mail_unknown_asks", 7, "mail",
             "Should an email to an unknown address come to you for approval?",
             True, ":green[:material/check:] Yes", icon="alternate_email"),
    Question("mail_files", 8, "mail",
             "Can the agent send a customer a file, such as a report or a payslip, without you seeing it first?",
             False, ":red[:material/close:] No", icon="attach_file"),
    Question("block_export", 9, "data",
             "Always block exporting the whole customer list?",
             True, ":green[:material/check:] Yes, blocked", icon="download"),
    Question("block_delete", 10, "data",
             "Always block deleting invoices or documents?",
             True, ":green[:material/check:] Yes, blocked", icon="delete"),
    Question("read_allowed", 11, "data",
             "Can the agent read information, such as invoices and customer details, without asking?",
             True, ":green[:material/check:] Yes, reading only is allowed", icon="visibility"),
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
    """Returns (clean answers, problems). `problems` are plain-English messages for the owner; empty = OK.

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
                problems.append(f"Question {number}: please choose an amount in shekels, above zero.")
            answers[follow_up.id] = amount
    auto, daily = answers["auto_limit_ils"], answers["daily_limit_ils"]
    if auto and daily and daily < auto:
        problems.append("Question 2: the daily limit must be at least the amount you chose in question 1.")

    # 3: supplier names (kept for the record; the engine cannot match a payee, see baseline.limitation_notes)
    names = [n.strip() for n in re.split(r"[,\n;]+", str(raw.get("supplier_names") or "")) if n.strip()]
    answers["supplier_names"] = names if answers["pay_trusted_suppliers"] else []
    if answers["pay_trusted_suppliers"] and not names:
        problems.append("Question 3: please type the names of your regular suppliers.")

    # 5: the office's own mail domain
    answers["company_domains"] = []
    if answers["mail_internal"]:
        domains, invalid = parse_domains(raw.get("internal_domain"))
        free = [d for d in domains if d in FREE_MAIL_DOMAINS]
        domains = [d for d in domains if d not in FREE_MAIL_DOMAINS]
        if invalid:
            problems.append(f"Question 5: couldn't read {', '.join(invalid)}. Type a domain like office.co.il.")
        elif free:
            problems.append(f"Question 5: {', '.join(free)} is a free email service. Allowing that domain would allow email to "
                            "every address on it. Type your office's own domain.")
        elif not domains:
            problems.append("Question 5: please type your office's domain, for example office.co.il.")
        answers["company_domains"] = domains

    # 6: fixed addresses -> allowed by domain
    answers["trusted_addresses"], answers["trusted_domains"] = [], []
    if answers["mail_trusted"]:
        entries, invalid = parse_addresses(raw.get("trusted_addresses"))
        usable = [(a, d) for a, d in entries if d not in FREE_MAIL_DOMAINS]
        excluded = [a for a, d in entries if d in FREE_MAIL_DOMAINS]
        if invalid:
            problems.append(f"Question 6: couldn't read {', '.join(invalid)}. Type an address like accountant@cpa-firm.co.il.")
        elif not entries:
            problems.append("Question 6: please type the fixed addresses.")
        elif not usable:
            problems.append("Question 6: all the addresses are on a free email service (for example gmail.com), and they can't be "
                            "allowed without allowing every address on that service. Emails to them will still need your approval.")
        elif excluded:
            warnings.append(f"The addresses {', '.join(excluded)} were not allowed: they are on a free email service, and allowing "
                            "them would cover every address on it. They are treated like any unknown address (question 7).")
        answers["trusted_addresses"] = [a for a, _ in usable]
        answers["trusted_domains"] = list(dict.fromkeys(d for _, d in usable))

    answers["warnings"] = warnings
    return answers, problems
