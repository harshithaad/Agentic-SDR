"""Gmail integration.

Fixes the reply-matching defect from the audit: Gmail's send API returns its
internal message id, but a reply's In-Reply-To header carries the RFC 2822
Message-ID — two different namespaces that never intersect. After sending we
fetch the message's metadata once to capture the real Message-ID header and
store it, so replies match on first-class data instead of surviving on the
thread-id fallback.
"""
import base64
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings


class GmailNotConfigured(Exception):
    pass


def _service():
    if not (settings.GMAIL_CLIENT_ID and settings.GMAIL_CLIENT_SECRET and settings.GMAIL_REFRESH_TOKEN):
        raise GmailNotConfigured("GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN not set")
    creds = Credentials(
        token=None,
        refresh_token=settings.GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GMAIL_CLIENT_ID,
        client_secret=settings.GMAIL_CLIENT_SECRET,
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def send_email(to: str, subject: str, body: str,
               in_reply_to_rfc_id: Optional[str] = None,
               thread_id: Optional[str] = None) -> Dict:
    """Send and return {'message_id', 'thread_id', 'rfc_message_id'}."""
    service = _service()
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["From"] = settings.GMAIL_SENDER_EMAIL
    msg["Subject"] = subject
    if in_reply_to_rfc_id:
        msg["In-Reply-To"] = in_reply_to_rfc_id
        msg["References"] = in_reply_to_rfc_id
    msg.attach(MIMEText(body, "plain"))

    payload: Dict = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    sent = service.users().messages().send(userId="me", body=payload).execute()

    # capture the RFC 2822 Message-ID for reliable reply matching
    rfc_message_id = ""
    try:
        meta = service.users().messages().get(
            userId="me", id=sent["id"], format="metadata", metadataHeaders=["Message-ID"]
        ).execute()
        for h in meta.get("payload", {}).get("headers", []):
            if h["name"].lower() == "message-id":
                rfc_message_id = h["value"]
                break
    except HttpError:
        pass  # thread-id matching still works as fallback

    return {
        "message_id": sent["id"],
        "thread_id": sent.get("threadId", ""),
        "rfc_message_id": rfc_message_id,
    }


def poll_replies(since_epoch: Optional[int] = None, max_results: int = 50) -> List[Dict]:
    """Inbox messages carrying In-Reply-To, i.e. replies to something we sent."""
    service = _service()
    query = "in:inbox"
    if since_epoch:
        query += f" after:{since_epoch}"
    listing = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    replies = []
    for m in listing.get("messages", []):
        msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        in_reply_to = headers.get("in-reply-to", "")
        if not in_reply_to:
            continue
        replies.append({
            "message_id": m["id"],
            "thread_id": msg.get("threadId", ""),
            "from": headers.get("from", ""),
            "subject": headers.get("subject", ""),
            "in_reply_to": in_reply_to,
            "body": _extract_body(msg["payload"]),
        })
    return replies


def check_bounce(thread_id: str) -> bool:
    """True if the thread contains a delivery-failure notification."""
    if not thread_id:
        return False
    service = _service()
    try:
        thread = service.users().threads().get(userId="me", id=thread_id).execute()
    except HttpError:
        return False
    for m in thread.get("messages", []):
        headers = {h["name"].lower(): h["value"] for h in m.get("payload", {}).get("headers", [])}
        sender = headers.get("from", "").lower()
        if "mailer-daemon" in sender or "postmaster" in sender:
            return True
    return False


def _extract_body(payload: Dict) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    if payload.get("mimeType", "").startswith("multipart"):
        for part in payload.get("parts", []):
            body = _extract_body(part)
            if body:
                return body
    return ""
