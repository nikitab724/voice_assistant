from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate
import base64
from zoneinfo import ZoneInfo
from typing import Any, Literal, Optional

from fastmcp import Context
from pydantic import BaseModel

from gmail_client import get_gmail_service
from googleapiclient.errors import HttpError
from user_context import get_user_timezone


def _coerce_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _get_effective_tz() -> ZoneInfo:
    tz_name = get_user_timezone() or "America/Chicago"
    try:
        return ZoneInfo(tz_name)
    except Exception:  # pragma: no cover
        return ZoneInfo("America/Chicago")


class GmailProfile(BaseModel):
    emailAddress: Optional[str] = None
    messagesTotal: Optional[int] = None
    threadsTotal: Optional[int] = None
    historyId: Optional[str] = None


class GmailProfileResult(BaseModel):
    status: Literal["success", "error"]
    profile: Optional[GmailProfile] = None
    message: Optional[str] = None


class GmailEmail(BaseModel):
    messageId: Optional[str] = None
    threadId: Optional[str] = None
    internalDateMs: Optional[int] = None
    receivedIso: Optional[str] = None
    snippet: Optional[str] = None
    from_: Optional[str] = None
    subject: Optional[str] = None
    date: Optional[str] = None
    labelIds: Optional[list[str]] = None
    isUnread: Optional[bool] = None


class ListGmailEmailsResult(BaseModel):
    status: Literal["success", "error"]
    startIso: str
    endIso: str
    lookbackHours: Optional[int] = None
    query: Optional[str] = None
    category: Optional[str] = None
    gmailQuery: Optional[str] = None
    emails: list[GmailEmail]
    message: Optional[str] = None


async def get_gmail_profile_tool(context: Context | None = None) -> dict[str, Any]:
    """
    Minimal Gmail connectivity check.
    Uses the per-request google_access_token and calls Gmail users.getProfile('me').
    """
    try:
        service = get_gmail_service()
        profile = service.users().getProfile(userId="me").execute()
        result = GmailProfileResult(
            status="success",
            profile=GmailProfile(
                emailAddress=profile.get("emailAddress"),
                messagesTotal=profile.get("messagesTotal"),
                threadsTotal=profile.get("threadsTotal"),
                historyId=profile.get("historyId"),
            ),
        )
        if context:
            await context.info(f"Gmail connected for {result.profile.emailAddress or 'unknown'}.")
        return result.model_dump()
    except Exception as exc:  # pragma: no cover
        msg = f"Failed to connect to Gmail: {exc}"
        if context:
            await context.error(msg)
        return GmailProfileResult(status="error", message=msg).model_dump()


async def list_gmail_emails_tool(
    *,
    start_iso: str | None = None,
    end_iso: str | None = None,
    lookback_hours: int | None = None,
    max_results: int = 10,
    query: str | None = None,
    unread_only: bool = True,
    category: str | None = "primary",
    context: Context | None = None,
) -> dict[str, Any]:
    """
    List Gmail emails within a time range.

    - Defaults to the last 12 hours if no start/end are provided.
    - Uses Gmail search query with after:/before: epoch seconds.
    - Returns lightweight metadata (From/Subject/Date/snippet).
    """
    try:
        tz = _get_effective_tz()
        now_local = datetime.now(tz)
        if end_iso:
            end_dt_local = _coerce_datetime(end_iso).astimezone(tz)
        else:
            end_dt_local = now_local

        if start_iso:
            start_dt_local = _coerce_datetime(start_iso).astimezone(tz)
        elif lookback_hours is not None:
            # Prefer explicit lookback for "past N hours" to avoid model generating wrong absolute times.
            lh = int(lookback_hours)
            if lh <= 0:
                raise ValueError("lookback_hours must be > 0")
            if lh > 168:
                lh = 168  # clamp to 7 days
            start_dt_local = end_dt_local - timedelta(hours=lh)
        else:
            start_dt_local = end_dt_local - timedelta(hours=12)

        if start_dt_local > end_dt_local:
            start_dt_local, end_dt_local = end_dt_local, start_dt_local

        # Clamp max_results to something reasonable
        max_results = max(1, min(int(max_results or 10), 50))

        # Gmail expects epoch seconds; use UTC timestamps for correctness.
        start_epoch = int(start_dt_local.astimezone(timezone.utc).timestamp())
        end_epoch = int(end_dt_local.astimezone(timezone.utc).timestamp())

        q_parts = [f"after:{start_epoch}", f"before:{end_epoch}"]

        # Gmail inbox tab filtering (Primary/Promotions/Social/Updates/Forums)
        if category:
            normalized = category.strip().lower()
            category_map = {
                "primary": "primary",
                "promotions": "promotions",
                "social": "social",
                "updates": "updates",
                "forums": "forums",
            }
            if normalized not in category_map:
                raise ValueError(
                    "Invalid category. Use one of: primary, promotions, social, updates, forums"
                )
            q_parts.append(f"category:{category_map[normalized]}")

        if unread_only:
            q_parts.append("is:unread")
        if query:
            q_parts.append(f"({query})")
        q = " ".join(q_parts)

        service = get_gmail_service()
        resp = (
            service.users()
            .messages()
            .list(userId="me", q=q, maxResults=max_results)
            .execute()
        )

        ids = [m.get("id") for m in (resp.get("messages") or []) if m.get("id")]

        emails: list[GmailEmail] = []
        for mid in ids:
            msg = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )

            headers = {h.get("name"): h.get("value") for h in (msg.get("payload", {}).get("headers") or [])}
            label_ids = msg.get("labelIds") or []
            is_unread = "UNREAD" in set(label_ids)
            received_iso = None
            if msg.get("internalDate"):
                received_dt = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc).astimezone(tz)
                received_iso = received_dt.isoformat()
            emails.append(
                GmailEmail(
                    messageId=msg.get("id"),
                    threadId=msg.get("threadId"),
                    internalDateMs=int(msg.get("internalDate")) if msg.get("internalDate") else None,
                    receivedIso=received_iso,
                    snippet=msg.get("snippet"),
                    from_=headers.get("From"),
                    subject=headers.get("Subject"),
                    date=headers.get("Date"),
                    labelIds=label_ids,
                    isUnread=is_unread,
                )
            )

        if context:
            await context.info(
                f"Fetched {len(emails)} emails from Gmail. Range: {start_dt_local.isoformat()} → {end_dt_local.isoformat()}. Query: {q}"
            )

        return ListGmailEmailsResult(
            status="success",
            startIso=start_dt_local.isoformat(),
            endIso=end_dt_local.isoformat(),
            lookbackHours=int(lookback_hours) if lookback_hours is not None else None,
            query=query,
            category=category,
            gmailQuery=q,
            emails=emails,
        ).model_dump()

    except Exception as exc:  # pragma: no cover
        msg = f"Failed to list Gmail emails: {exc}"
        if context:
            await context.error(msg)
        # Best-effort timestamps for the result
        now = datetime.now(timezone.utc).isoformat()
        return ListGmailEmailsResult(
            status="error",
            startIso=start_iso or now,
            endIso=end_iso or now,
            lookbackHours=int(lookback_hours) if lookback_hours is not None else None,
            query=query,
            category=category,
            gmailQuery=None,
            emails=[],
            message=msg,
        ).model_dump()


class MarkEmailsReadResult(BaseModel):
    status: Literal["success", "error"]
    updatedIds: list[str] = []
    message: Optional[str] = None


async def mark_gmail_emails_read_tool(
    *,
    message_ids: list[str],
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Mark Gmail messages as read by removing the UNREAD label.
    Requires gmail.modify scope.
    """
    if not message_ids:
        return MarkEmailsReadResult(status="error", message="message_ids is required").model_dump()

    try:
        service = get_gmail_service()
        updated: list[str] = []
        failed: list[str] = []
        for mid in message_ids:
            if not mid:
                continue
            try:
                # Primary: treat as messageId
                service.users().messages().modify(
                    userId="me",
                    id=mid,
                    body={"removeLabelIds": ["UNREAD"]},
                ).execute()
                updated.append(mid)
            except HttpError as exc:
                # If the model accidentally passed a threadId, try threads.modify as fallback.
                if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 404:
                    try:
                        service.users().threads().modify(
                            userId="me",
                            id=mid,
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute()
                        updated.append(mid)
                    except Exception:
                        failed.append(mid)
                else:
                    failed.append(mid)

        if context:
            await context.info(f"Marked {len(updated)} emails as read.")

        if failed:
            return MarkEmailsReadResult(
                status="error",
                updatedIds=updated,
                message=f"Some IDs could not be marked as read (possibly stale or wrong type): {failed[:5]}{'...' if len(failed) > 5 else ''}",
            ).model_dump()

        return MarkEmailsReadResult(status="success", updatedIds=updated).model_dump()

    except Exception as exc:  # pragma: no cover
        msg = f"Failed to mark emails as read: {exc}"
        if context:
            await context.error(msg)
        return MarkEmailsReadResult(status="error", message=msg).model_dump()


class CreateDraftResult(BaseModel):
    status: Literal["success", "error"]
    draftId: Optional[str] = None
    messageId: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    preview: Optional[str] = None
    message: Optional[str] = None


def _build_rfc822_email(
    *,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    bcc: Optional[str] = None,
) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    if cc:
        msg["Cc"] = cc
    if bcc:
        # Bcc header is typically omitted from the final message, but for drafts it's okay to include.
        msg["Bcc"] = bcc
    msg.set_content(body or "")
    return msg.as_string()


async def create_gmail_draft_tool(
    *,
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Create a Gmail draft email (does NOT send).
    """
    if not to:
        return CreateDraftResult(status="error", message="to is required").model_dump()
    if not subject:
        return CreateDraftResult(status="error", message="subject is required").model_dump()

    try:
        service = get_gmail_service()
        raw = _build_rfc822_email(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
        raw_b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

        created = (
            service.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": raw_b64}})
            .execute()
        )

        draft_id = created.get("id")
        msg_id = (created.get("message") or {}).get("id")

        preview = (body or "").strip().replace("\n", " ")
        if len(preview) > 200:
            preview = preview[:200] + "…"

        if context:
            await context.info(f"Created Gmail draft {draft_id or '(unknown)'} to {to}.")

        return CreateDraftResult(
            status="success",
            draftId=draft_id,
            messageId=msg_id,
            to=to,
            subject=subject,
            preview=preview,
        ).model_dump()

    except Exception as exc:  # pragma: no cover
        msg = f"Failed to create Gmail draft: {exc}"
        if context:
            await context.error(msg)
        return CreateDraftResult(status="error", message=msg).model_dump()


class SendDraftResult(BaseModel):
    status: Literal["success", "error"]
    draftId: Optional[str] = None
    messageId: Optional[str] = None
    message: Optional[str] = None


async def send_gmail_draft_tool(
    *,
    draft_id: str,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Send an existing Gmail draft. Requires gmail.send scope.
    """
    if not draft_id:
        return SendDraftResult(status="error", message="draft_id is required").model_dump()

    try:
        service = get_gmail_service()
        sent = service.users().drafts().send(userId="me", body={"id": draft_id}).execute()
        msg_id = (sent.get("message") or {}).get("id")

        if context:
            await context.info(f"Sent Gmail draft {draft_id}.")

        return SendDraftResult(status="success", draftId=draft_id, messageId=msg_id).model_dump()
    except Exception as exc:  # pragma: no cover
        msg = f"Failed to send Gmail draft: {exc}"
        if context:
            await context.error(msg)
        return SendDraftResult(status="error", message=msg).model_dump()


