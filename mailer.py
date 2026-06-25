"""Outbound email via SMTP (Titan) + IMAP reply polling."""

from __future__ import annotations

import email
import imaplib
import logging
import os
import random
import re
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any

import env_loader  # noqa: F401 — load .env at import

import email_settings

log = logging.getLogger("mailer")

_DEFAULT_SMTP_HOST = "smtp.titan.email"
_DEFAULT_SMTP_PORT = 465
_DEFAULT_IMAP_HOST = "imap.titan.email"
_DEFAULT_IMAP_PORT = 993

EMAIL_DAILY_LIMIT = int(os.environ.get("EMAIL_DAILY_LIMIT", "40"))
EMAIL_MIN_INTERVAL_SECONDS = int(os.environ.get("EMAIL_MIN_INTERVAL_SECONDS", "90"))
EMAIL_SEND_WINDOW = (os.environ.get("EMAIL_SEND_WINDOW") or "").strip()
IMAP_POLL_SECONDS = int(os.environ.get("IMAP_POLL_SECONDS", "300"))


def _cfg(key: str, default: str = "") -> str:
    return (os.environ.get(key) or default).strip()


def simulation_enabled() -> bool:
    return bool(email_settings.load_settings().get("simulation_enabled"))


def simulation_to() -> str:
    return (email_settings.load_settings().get("simulation_to") or _default_to()).strip()


def _default_to() -> str:
    return (os.environ.get("EMAIL_SIMULATION_TO") or "ghamari2004@gmail.com").strip()


def set_simulation_enabled(enabled: bool) -> dict[str, Any]:
    email_settings.save_settings(simulation_enabled=enabled)
    if enabled:
        _reset_no_recipient_failures()
    return simulation_info()


def _reset_no_recipient_failures() -> int:
    """Allow retry of rows that failed only because the company has no email."""
    import db

    return db.heal_no_recipient_failures()


def simulation_info() -> dict[str, Any]:
    settings = email_settings.load_settings()
    enabled = bool(settings.get("simulation_enabled"))
    to_addr = (settings.get("simulation_to") or _default_to()).strip()
    return {
        "enabled": enabled,
        "to": to_addr or None,
        "ready": not enabled or bool(to_addr),
    }


def resolve_send_to(stored_recipient: str | None) -> tuple[str, bool]:
    """Return (smtp_to, is_simulation). Reads settings fresh each call."""
    settings = email_settings.load_settings()
    if settings.get("simulation_enabled"):
        to = (settings.get("simulation_to") or _default_to()).strip()
        if to:
            return to, True
    return (stored_recipient or "").strip(), False


def display_recipient(stored_recipient: str | None) -> str:
    """UI / preview recipient — simulation override without touching DB."""
    to, _sim = resolve_send_to(stored_recipient)
    return to


def outbound_recipient(stored_recipient: str | None) -> str:
    """Actual SMTP recipient for send."""
    to, _sim = resolve_send_to(stored_recipient)
    return to


def smtp_config() -> dict[str, Any]:
    user = _cfg("SMTP_USER")
    password = _cfg("SMTP_PASSWORD")
    from_addr = _cfg("SMTP_FROM") or user
    host = _cfg("SMTP_HOST", _DEFAULT_SMTP_HOST)
    port_raw = _cfg("SMTP_PORT") or str(_DEFAULT_SMTP_PORT)
    try:
        port = int(port_raw)
    except ValueError:
        port = _DEFAULT_SMTP_PORT
    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from": from_addr,
    }


def imap_config() -> dict[str, Any]:
    cfg = smtp_config()
    return {
        "host": _cfg("IMAP_HOST", _DEFAULT_IMAP_HOST),
        "port": int(_cfg("IMAP_PORT") or str(_DEFAULT_IMAP_PORT)),
        "user": cfg["user"],
        "password": cfg["password"],
    }


def email_status(*, verify: bool = False) -> dict[str, Any]:
    cfg = smtp_config()
    issues: list[str] = []
    if not cfg["user"]:
        issues.append("SMTP_USER is not set in .env")
    if not cfg["password"]:
        issues.append("SMTP_PASSWORD is not set in .env")
    if not cfg["from"]:
        issues.append("SMTP_FROM is not set in .env")
    sim = simulation_info()
    if sim["enabled"] and not sim["to"]:
        issues.append("EMAIL_SIMULATION=1 but EMAIL_SIMULATION_TO is empty")
    ready = not issues
    out: dict[str, Any] = {
        "ready": ready,
        "from": cfg["from"] or None,
        "host": cfg["host"],
        "port": cfg["port"],
        "issues": issues,
        "verified": False,
        "imap_host": imap_config()["host"],
        "rate": rate_status(),
        "simulation": simulation_info(),
    }
    if verify and ready:
        try:
            _smtp_connect(cfg)
            out["verified"] = True
        except Exception as e:
            out["ready"] = False
            out["issues"] = [str(e)]
    return out


def rate_status(*, sent_today: int | None = None, last_sent_at: int | None = None) -> dict[str, Any]:
    import db

    if sent_today is None:
        sent_today = db.count_emails_sent_today()
    if last_sent_at is None:
        last_sent_at = db.get_last_email_sent_at()
    in_window = _in_send_window()
    daily_ok = sent_today < EMAIL_DAILY_LIMIT
    interval_ok = True
    wait_seconds = 0
    if last_sent_at:
        elapsed = int(time.time()) - last_sent_at
        min_wait = _jittered_interval()
        if elapsed < min_wait:
            interval_ok = False
            wait_seconds = min_wait - elapsed
    return {
        "daily_limit": EMAIL_DAILY_LIMIT,
        "sent_today": sent_today,
        "daily_remaining": max(0, EMAIL_DAILY_LIMIT - sent_today),
        "min_interval_seconds": EMAIL_MIN_INTERVAL_SECONDS,
        "in_send_window": in_window,
        "can_send": daily_ok and interval_ok and in_window,
        "wait_seconds": wait_seconds,
        "send_window": EMAIL_SEND_WINDOW or None,
    }


def can_send_now() -> tuple[bool, str]:
    import db

    status = rate_status(
        sent_today=db.count_emails_sent_today(),
        last_sent_at=db.get_last_email_sent_at(),
    )
    if not status["in_send_window"]:
        return False, "Outside send window"
    if status["sent_today"] >= EMAIL_DAILY_LIMIT:
        return False, f"Daily limit reached ({EMAIL_DAILY_LIMIT})"
    if status["wait_seconds"] > 0:
        return False, f"Rate limit — wait {status['wait_seconds']}s"
    return True, ""


def _jittered_interval() -> int:
    base = max(30, EMAIL_MIN_INTERVAL_SECONDS)
    jitter = random.uniform(0.5, 1.5)
    return int(base * jitter)


def _in_send_window() -> bool:
    if not EMAIL_SEND_WINDOW or "-" not in EMAIL_SEND_WINDOW:
        return True
    try:
        start_s, end_s = EMAIL_SEND_WINDOW.split("-", 1)
        now = datetime.now()
        start_h, start_m = [int(x) for x in start_s.strip().split(":")]
        end_h, end_m = [int(x) for x in end_s.strip().split(":")]
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        cur = now.hour * 60 + now.minute
        if start_min <= end_min:
            return start_min <= cur <= end_min
        return cur >= start_min or cur <= end_min
    except Exception:
        return True


def _smtp_connect(cfg: dict[str, Any]) -> smtplib.SMTP:
    host = cfg["host"]
    port = int(cfg["port"])
    user = cfg["user"]
    password = cfg["password"]
    if port == 465:
        ctx = ssl.create_default_context()
        server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(host, port, timeout=30)
        if port == 587:
            ctx = ssl.create_default_context()
            server.starttls(context=ctx)
    server.login(user, password)
    return server


def make_outbound_message_id(from_addr: str) -> str:
    domain = (from_addr or "localhost").split("@")[-1] or "localhost"
    return make_msgid(domain=domain)


def is_hard_bounce_error(exc: Exception) -> bool:
    text = str(exc).lower()
    patterns = (
        "550", "551", "552", "553", "554",
        "user unknown", "mailbox not found", "does not exist",
        "invalid recipient", "no such user", "recipient rejected",
    )
    return any(p in text for p in patterns)


def send_email(
    *,
    to: str,
    subject: str,
    body: str,
    reply_to: str | None = None,
) -> str:
    """Simple plain-text send. Returns Message-ID header value."""
    cfg = smtp_config()
    if not cfg["user"] or not cfg["password"]:
        raise RuntimeError("SMTP is not configured — set SMTP_USER and SMTP_PASSWORD in .env")
    recipient = (to or "").strip()
    if not recipient or "@" not in recipient:
        raise ValueError("Invalid recipient email")
    msg_id = make_outbound_message_id(cfg["from"])
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = recipient
    msg["Subject"] = (subject or "").strip()
    msg["Message-ID"] = msg_id
    msg["Date"] = formatdate(localtime=True)
    msg["List-Unsubscribe"] = f"<mailto:{cfg['from']}?subject=unsubscribe>"
    if reply_to:
        msg["Reply-To"] = reply_to.strip()
    else:
        msg["Reply-To"] = cfg["from"]
    msg.set_content((body or "").strip())

    server = _smtp_connect(cfg)
    try:
        server.send_message(msg)
        log.info("email sent to %s subject=%r", recipient, subject[:80])
    finally:
        server.quit()
    return msg_id


def send_campaign_email(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    inline_images: list[tuple[str, str, str]] | None = None,
) -> str:
    """Send multipart email with optional inline CID images. Returns Message-ID."""
    cfg = smtp_config()
    recipient = (to or "").strip()
    if not recipient:
        raise ValueError("Invalid recipient email")

    msg_id = make_outbound_message_id(cfg["from"])
    msg = EmailMessage()
    msg["From"] = cfg["from"]
    msg["To"] = recipient
    msg["Subject"] = (subject or "").strip()
    msg["Message-ID"] = msg_id
    msg["Date"] = formatdate(localtime=True)
    msg["Reply-To"] = cfg["from"]
    # No List-Unsubscribe / Precedence headers: for 1:1 outreach they explicitly
    # flag the message as bulk and push it to Gmail's Promotions tab.

    msg.set_content(body_text or "")
    msg.add_alternative(body_html or body_text or "", subtype="html")

    if inline_images:
        html_part = msg.get_payload()[-1]
        for cid, path, mime_subtype in inline_images:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            maintype = "image"
            html_part.add_related(data, maintype=maintype, subtype=mime_subtype, cid=f"<{cid}>")

    server = _smtp_connect(cfg)
    try:
        server.send_message(msg)
        log.info("campaign email sent to %s", recipient)
    finally:
        server.quit()
    return msg_id


def _imap_connect() -> imaplib.IMAP4_SSL:
    cfg = imap_config()
    if not cfg["user"] or not cfg["password"]:
        raise RuntimeError("IMAP not configured")
    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], ssl_context=ctx)
    imap.login(cfg["user"], cfg["password"])
    return imap


def _extract_header(msg: email.message.Message, name: str) -> str:
    val = msg.get(name, "") or ""
    if isinstance(val, email.header.Header):
        return str(val)
    return str(val).strip()


def _message_snippet(msg: email.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return (part.get_payload(decode=True) or b"").decode("utf-8", errors="replace")[:1000]
                except Exception:
                    pass
    try:
        return (msg.get_payload(decode=True) or b"").decode("utf-8", errors="replace")[:1000]
    except Exception:
        return ""


def _normalize_msg_id(raw: str) -> str:
    return (raw or "").strip().strip("<>")


def poll_imap_inbox(*, since_days: int = 14) -> list[dict[str, Any]]:
    """Fetch recent inbox messages for reply/bounce/unsubscribe detection."""
    import db

    results: list[dict[str, Any]] = []
    try:
        imap = _imap_connect()
    except Exception as e:
        log.warning("IMAP connect failed: %s", e)
        return results
    try:
        imap.select("INBOX")
        _, data = imap.search(None, "ALL")
        ids = (data[0] or b"").split()
        ids = ids[-100:] if len(ids) > 100 else ids
        for num in reversed(ids):
            _, msg_data = imap.fetch(num, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)
            from_addr = _extract_header(msg, "From")
            subject = _extract_header(msg, "Subject")
            in_reply_to = _extract_header(msg, "In-Reply-To")
            references = _extract_header(msg, "References")
            msg_id = _extract_header(msg, "Message-ID")
            snippet = _message_snippet(msg)
            received = int(time.time())

            combined = f"{subject} {snippet}".lower()
            if any(x in combined for x in ("unsubscribe", "avregistrera", "avregistrer")):
                email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", from_addr)
                if email_match:
                    db.add_email_suppression(email_match.group(0).lower(), None, "unsubscribe")
                continue

            parent_ids = []
            if in_reply_to:
                parent_ids.append(_normalize_msg_id(in_reply_to))
            for ref in (references or "").split():
                parent_ids.append(_normalize_msg_id(ref))

            matched = None
            for pid in parent_ids:
                if not pid:
                    continue
                matched = db.find_email_message_by_message_id(f"<{pid}>")
                if matched:
                    break
            if matched:
                db.record_email_reply(
                    campaign_email_message_id=matched["id"],
                    message_id=msg_id,
                    in_reply_to=in_reply_to or None,
                    from_addr=from_addr,
                    subject=subject,
                    snippet=snippet,
                    received_at=received,
                )
                results.append({"type": "reply", "orgnr": matched.get("orgnr")})
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return results
