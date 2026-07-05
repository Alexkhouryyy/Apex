"""Email tool — IMAP inbox reading + SMTP sending, pure stdlib.

Reading is safe and direct. Sending is NOT exposed to the agent directly: the
agent stages a draft via agent.approvals.stage("email", ...) and the user
approves it from the dashboard, at which point approvals._apply calls send().

Config (see config.py / .env):
  EMAIL_ADDRESS, EMAIL_PASSWORD
  EMAIL_IMAP_HOST (default derived), EMAIL_IMAP_PORT (993)
  EMAIL_SMTP_HOST, EMAIL_SMTP_PORT (587)

Use an app-specific password (Gmail/Outlook), never your main account password.
"""
from __future__ import annotations

import email
import imaplib
import smtplib
import ssl
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, formataddr
from typing import Optional


def _cfg() -> dict:
    import config
    addr = getattr(config, "EMAIL_ADDRESS", "") or ""
    domain = addr.split("@")[-1].lower() if "@" in addr else ""
    # Sensible defaults for the common providers; override in .env if needed.
    imap_default = {"gmail.com": "imap.gmail.com", "outlook.com": "outlook.office365.com",
                    "hotmail.com": "outlook.office365.com"}.get(domain, f"imap.{domain}" if domain else "")
    smtp_default = {"gmail.com": "smtp.gmail.com", "outlook.com": "smtp.office365.com",
                    "hotmail.com": "smtp.office365.com"}.get(domain, f"smtp.{domain}" if domain else "")
    return {
        "address": addr,
        "password": getattr(config, "EMAIL_PASSWORD", "") or "",
        "imap_host": getattr(config, "EMAIL_IMAP_HOST", "") or imap_default,
        "imap_port": int(getattr(config, "EMAIL_IMAP_PORT", 993) or 993),
        "smtp_host": getattr(config, "EMAIL_SMTP_HOST", "") or smtp_default,
        "smtp_port": int(getattr(config, "EMAIL_SMTP_PORT", 587) or 587),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["address"] and c["password"] and c["imap_host"])


def _decode(value) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _body_of(msg) -> str:
    """Extract a plain-text body from an email.message.Message."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                except Exception:
                    continue
        # Fall back to HTML stripped crudely
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    import re
                    html = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", "replace")
                    return re.sub(r"<[^>]+>", " ", html)
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", "replace")
    except Exception:
        return str(msg.get_payload())


def fetch_inbox(limit: int = 20, unread_only: bool = False) -> list[dict]:
    """Return recent inbox messages (newest first) as lightweight dicts."""
    if not is_configured():
        return [{"error": "Email not configured. Set EMAIL_ADDRESS / EMAIL_PASSWORD in .env."}]
    c = _cfg()
    try:
        M = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], ssl_context=ssl.create_default_context(), timeout=30)
        M.login(c["address"], c["password"])
        M.select("INBOX")
        criterion = "(UNSEEN)" if unread_only else "ALL"
        typ, data = M.search(None, criterion)
        ids = data[0].split()
        ids = ids[-limit:][::-1]  # newest first
        out = []
        for uid in ids:
            typ, msg_data = M.fetch(uid, "(BODY.PEEK[HEADER] FLAGS)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            flags = ""
            for part in msg_data:
                if isinstance(part, tuple) and b"FLAGS" in (part[0] or b""):
                    flags = part[0].decode("utf-8", "replace")
            name, addr = parseaddr(_decode(msg.get("From")))
            out.append({
                "uid": uid.decode(),
                "from": formataddr((name, addr)) if name else addr,
                "from_email": addr,
                "subject": _decode(msg.get("Subject")) or "(no subject)",
                "date": _decode(msg.get("Date")),
                "unread": "\\Seen" not in flags,
            })
        M.logout()
        return out
    except Exception as e:
        return [{"error": f"IMAP error: {e}"}]


def read_message(uid: str) -> dict:
    """Fetch one message's full body by UID (marks it read)."""
    if not is_configured():
        return {"error": "Email not configured."}
    c = _cfg()
    try:
        M = imaplib.IMAP4_SSL(c["imap_host"], c["imap_port"], ssl_context=ssl.create_default_context(), timeout=30)
        M.login(c["address"], c["password"])
        M.select("INBOX")
        typ, msg_data = M.fetch(uid.encode(), "(RFC822)")
        if not msg_data or not msg_data[0]:
            M.logout()
            return {"error": f"Message {uid} not found."}
        msg = email.message_from_bytes(msg_data[0][1])
        name, addr = parseaddr(_decode(msg.get("From")))
        result = {
            "uid": uid,
            "from": formataddr((name, addr)) if name else addr,
            "from_email": addr,
            "to": _decode(msg.get("To")),
            "subject": _decode(msg.get("Subject")) or "(no subject)",
            "date": _decode(msg.get("Date")),
            "message_id": msg.get("Message-ID", ""),
            "body": _body_of(msg).strip()[:8000],
        }
        M.logout()
        return result
    except Exception as e:
        return {"error": f"IMAP read error: {e}"}


def send(to: str, subject: str, body: str, in_reply_to: Optional[str] = None) -> str:
    """Low-level SMTP send. Called ONLY by approvals._apply after user approval."""
    if not is_configured():
        return "Email not configured — cannot send."
    c = _cfg()
    if not c["smtp_host"]:
        return "EMAIL_SMTP_HOST not set — cannot send."
    try:
        m = EmailMessage()
        m["From"] = c["address"]
        m["To"] = to
        m["Subject"] = subject
        if in_reply_to:
            m["In-Reply-To"] = in_reply_to
            m["References"] = in_reply_to
        m.set_content(body)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(c["smtp_host"], c["smtp_port"], timeout=30) as s:
            s.starttls(context=ctx)
            s.login(c["address"], c["password"])
            s.send_message(m)
        return f"Sent email to {to} (subject: {subject})."
    except Exception as e:
        return f"SMTP send failed: {e}"
