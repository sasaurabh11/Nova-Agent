from __future__ import annotations

import imaplib
import re
import smtplib
from dataclasses import dataclass, field
from email import message_from_bytes
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr

from backend.config import get_config

_ATTACH_EXT = {".pdf", ".png", ".jpg", ".jpeg"}


def mailbox_uidnext() -> int:
    """The UID the NEXT new message will get. The poller records this at startup as
    a baseline, then only fetches mail with UID >= it — so the historical backlog is
    ignored and only genuinely new arrivals are processed (forever)."""
    cfg = get_config()
    if not cfg.email_configured:
        return 1
    imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        imap.login(cfg.email_user, cfg.email_password)
        status, data = imap.status(cfg.imap_folder, "(UIDNEXT)")
        if status == "OK" and data and data[0]:
            m = re.search(rb"UIDNEXT\s+(\d+)", data[0])
            if m:
                return int(m.group(1))
        return 1
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class FetchedEmail:
    message_id: str
    sender: str
    subject: str
    attachments: list[tuple[str, bytes]] = field(default_factory=list)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001
        return value


def fetch_new_emails(min_uid: int | None = None,
                     limit: int | None = None) -> tuple[int, list[FetchedEmail]]:
    """Fetch UNSEEN messages with UID >= min_uid (i.e. arrived after the poller's
    baseline). Reading them marks them \\Seen. Returns (messages_fetched, emails).
    A custom IMAP_SEARCH overrides the UID range (advanced targeting)."""
    cfg = get_config()
    if not cfg.email_configured:
        return 0, []
    custom = cfg.imap_search.strip()
    if not custom and min_uid is None:
        return 0, []  # never sweep the backlog without a baseline
    cap = cfg.max_fetch_per_poll if limit is None else min(cfg.max_fetch_per_poll, limit)

    out: list[FetchedEmail] = []
    fetched = 0
    imap = imaplib.IMAP4_SSL(cfg.imap_host, cfg.imap_port)
    try:
        imap.login(cfg.email_user, cfg.email_password)
        imap.select(cfg.imap_folder)
        if custom:
            status, data = imap.uid("search", None, custom)
        else:
            status, data = imap.uid("search", None, "UNSEEN", "UID", f"{min_uid}:*")
        if status != "OK" or not data or not data[0]:
            return 0, []
        uids = data[0].split()
        # IMAP quirk: "N:*" also returns the highest UID even if < N — filter it out.
        if not custom:
            uids = [u for u in uids if int(u) >= min_uid]
        if not uids:
            return 0, []
        if len(uids) > cap:
            uids = uids[-cap:]  # newest only
        for uid in uids:
            fetched += 1
            status, msg_data = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = message_from_bytes(msg_data[0][1])
            attachments: list[tuple[str, bytes]] = []
            for part in msg.walk():
                if part.get_content_maintype() == "multipart":
                    continue
                filename = part.get_filename()
                if not filename:
                    continue
                name = _decode(filename)
                if not any(name.lower().endswith(e) for e in _ATTACH_EXT):
                    continue
                payload = part.get_payload(decode=True)
                if payload:
                    attachments.append((name, payload))
            if not attachments:
                continue  # ignore mail with no trade-doc attachments
            out.append(FetchedEmail(
                message_id=(msg.get("Message-ID") or "").strip(),
                sender=parseaddr(msg.get("From"))[1] or "unknown@unknown",
                subject=_decode(msg.get("Subject")) or "(no subject)",
                attachments=attachments,
            ))
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return fetched, out


def send_reply(to_addr: str, subject: str, body: str, in_reply_to: str | None = None) -> None:
    cfg = get_config()
    if not cfg.email_configured:
        raise RuntimeError("email is not configured")

    msg = EmailMessage()
    msg["From"] = cfg.email_user
    msg["To"] = to_addr
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    msg.set_content(body)

    with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.llm_timeout_s) as smtp:
        smtp.starttls()
        smtp.login(cfg.email_user, cfg.email_password)
        smtp.send_message(msg)
