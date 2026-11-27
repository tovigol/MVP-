# email_server.py
import json
import os
import smtplib
from datetime import datetime, timezone
from email.header import Header
from email.mime.text import MIMEText
from email.utils import getaddresses, make_msgid
from pathlib import Path
from dotenv import load_dotenv
from fastmcp import FastMCP

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env", override=True)

SENT_LOG_PATH = BASE_DIR / "sent_emails.jsonl"
mcp = FastMCP("email")


def _parse_and_validate_recipients(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    raw_items = [value] if isinstance(value, str) else [str(item) for item in value]
    for raw in raw_items:
        if any(c in raw for c in "\r\n"):
            raise ValueError(f"Invalid email address (newline detected): {raw!r}")

    parsed = getaddresses(raw_items)
    validated: list[str] = []
    for _, email in parsed:
        email = email.strip()
        local, sep, domain = email.partition("@")
        if not sep or not local or not domain or any(ch.isspace() for ch in email):
            raise ValueError(f"Invalid email address: {email!r}")
        validated.append(email)
    return validated


def _log_email(
    status: str,
    provider: str,
    to_list: list[str],
    cc_list: list[str],
    bcc_list: list[str],
    subject: str,
    message_id: str = "",
    error: str = "",
) -> None:
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "provider": provider,
        "to": to_list,
        "cc": cc_list,
        "bcc": bcc_list,
        "subject": subject,
        "message_id": message_id,
        "error": error,
    }
    with SENT_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _send_via_resend(to_list: list[str], cc_list: list[str], bcc_list: list[str], subject: str, body: str) -> str:
    import resend

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY is missing in .env")

    resend.api_key = api_key
    payload: dict = {
        "from": os.getenv("EMAIL_FROM", "onboarding@resend.dev"),
        "to": to_list,
        "subject": subject,
        "text": body,
    }
    if cc_list:
        payload["cc"] = cc_list
    if bcc_list:
        payload["bcc"] = bcc_list

    resp = resend.Emails.send(payload)
    if isinstance(resp, dict):
        return str(resp.get("id", "sent"))
    return str(getattr(resp, "id", "sent"))


def _send_via_smtp(to_list: list[str], cc_list: list[str], bcc_list: list[str], subject: str, body: str) -> str:
    host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASS")
    if not user or not password:
        raise RuntimeError("SMTP_USER or SMTP_PASS is missing in .env")

    msg = MIMEText(body, "plain", "utf-8")
    msg_id = make_msgid()
    msg["Message-ID"] = msg_id
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)

    all_recipients = to_list + cc_list + bcc_list
    with smtplib.SMTP(host, port, timeout=10) as server:
        server.starttls()
        server.login(user, password)
        server.sendmail(user, all_recipients, msg.as_string())

    return msg_id


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def send_email(
    to: str | list[str],
    subject: str,
    body: str,
    cc: str | list[str] = "",
    bcc: str | list[str] = "",
) -> dict:
    """Send an email to one or more recipients.

    Args:
        to: Recipient email address (or list/comma-separated addresses).
        subject: Subject line of the email.
        body: Plain-text body of the email.
        cc: Optional CC recipient email address(es).
        bcc: Optional BCC recipient email address(es).
    """
    if any(c in subject for c in "\r\n"):
        raise ValueError("Invalid subject: newlines are not allowed")

    to_list = _parse_and_validate_recipients(to)
    cc_list = _parse_and_validate_recipients(cc)
    bcc_list = _parse_and_validate_recipients(bcc)

    if not to_list:
        raise ValueError("At least one 'to' email address is required")

    provider = os.getenv("EMAIL_PROVIDER", "").strip().lower()
    if not provider:
        provider = "resend" if os.getenv("RESEND_API_KEY") else "smtp"

    try:
        if provider == "resend":
            msg_id = _send_via_resend(to_list, cc_list, bcc_list, subject, body)
        elif provider == "smtp":
            msg_id = _send_via_smtp(to_list, cc_list, bcc_list, subject, body)
        else:
            raise ValueError(f"Unsupported EMAIL_PROVIDER: {provider}")
    except Exception as exc:
        _log_email("failed", provider, to_list, cc_list, bcc_list, subject, error=str(exc))
        raise

    _log_email("sent", provider, to_list, cc_list, bcc_list, subject, message_id=msg_id)
    return {
        "status": "sent",
        "provider": provider,
        "message_id": msg_id,
        "to": to_list,
        "cc": cc_list,
        "bcc": bcc_list,
        "subject": subject,
    }


if __name__ == "__main__":
    mcp.run()