from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, getaddresses
import asyncio
import base64
import time
import difflib
import re
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
        profile_req = service.users().getProfile(userId="me")
        profile = await _execute_google_request(profile_req)
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
        list_req = service.users().messages().list(userId="me", q=q, maxResults=max_results)
        resp = await _execute_google_request(list_req)

        ids = [m.get("id") for m in (resp.get("messages") or []) if m.get("id")]

        emails: list[GmailEmail] = []
        for mid in ids:
            get_req = service.users().messages().get(
                userId="me",
                id=mid,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"],
            )
            msg = await _execute_google_request(get_req)

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
                modify_req = service.users().messages().modify(
                    userId="me",
                    id=mid,
                    body={"removeLabelIds": ["UNREAD"]},
                )
                await _execute_google_request(modify_req)
                updated.append(mid)
            except HttpError as exc:
                # If the model accidentally passed a threadId, try threads.modify as fallback.
                if getattr(exc, "resp", None) is not None and getattr(exc.resp, "status", None) == 404:
                    try:
                        thread_req = service.users().threads().modify(
                            userId="me",
                            id=mid,
                            body={"removeLabelIds": ["UNREAD"]},
                        )
                        await _execute_google_request(thread_req)
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
    fromEmail: Optional[str] = None
    to: Optional[str] = None
    cc: Optional[str] = None
    bcc: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
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
        # Fetch "from" address for UI display (fast call)
        try:
            profile_req = service.users().getProfile(userId="me")
            profile = await _execute_google_request(profile_req)
            from_email = profile.get("emailAddress")
        except Exception:
            from_email = None

        raw = _build_rfc822_email(to=to, subject=subject, body=body, cc=cc, bcc=bcc)
        raw_b64 = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

        create_req = service.users().drafts().create(userId="me", body={"message": {"raw": raw_b64}})
        created = await _execute_google_request(create_req)

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
            fromEmail=from_email,
            to=to,
            cc=cc,
            bcc=bcc,
            subject=subject,
            body=body,
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
        send_req = service.users().drafts().send(userId="me", body={"id": draft_id})
        sent = await _execute_google_request(send_req)
        msg_id = (sent.get("message") or {}).get("id")

        if context:
            await context.info(f"Sent Gmail draft {draft_id}.")

        return SendDraftResult(status="success", draftId=draft_id, messageId=msg_id).model_dump()
    except Exception as exc:  # pragma: no cover
        msg = f"Failed to send Gmail draft: {exc}"
        if context:
            await context.error(msg)
        return SendDraftResult(status="error", message=msg).model_dump()


class GmailContact(BaseModel):
    email: str
    name: Optional[str] = None
    totalCount: int
    sentCount: int
    receivedCount: int
    lastSeenIso: Optional[str] = None


class ListGmailContactsResult(BaseModel):
    status: Literal["success", "error"]
    lookbackDays: int
    maxMessagesScanned: int
    query: Optional[str] = None
    contacts: list[GmailContact]
    message: Optional[str] = None


def _extract_addresses(header_value: str | None) -> list[tuple[str, str]]:
    """
    Parse an RFC822 address header like:
      'John Doe <john@example.com>, "Jane" <jane@x.com>'
    Returns list of (display_name, email).
    """
    if not header_value:
        return []
    pairs = getaddresses([header_value])
    out: list[tuple[str, str]] = []
    for name, email in pairs:
        email = (email or "").strip()
        name = (name or "").strip().strip('"')
        if not email:
            continue
        out.append((name, email))
    return out


async def _execute_google_request(req) -> Any:
    """
    googleapiclient's .execute() is blocking; run it off the event loop thread so SSE streaming
    and keep-alives aren't frozen during large scans.
    """
    return await asyncio.to_thread(req.execute)


async def list_gmail_contacts_tool(
    *,
    query: str | None = None,
    lookback_days: int = 90,
    max_messages: int = 60,
    max_contacts: int = 50,
    exclude_no_reply: bool = True,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Build a lightweight "contact list" from your recent Gmail messages by scanning headers.

    Useful when the user says "email John" without giving an address:
    - For SENT mail: pulls recipients from To/Cc
    - For INBOX mail: pulls senders from From
    """
    try:
        service = get_gmail_service()
        tz = _get_effective_tz()
        started = time.monotonic()
        time_budget_s = 7.0

        lookback_days = max(1, int(lookback_days))
        max_messages = max(1, min(int(max_messages), 500))
        max_contacts = max(1, min(int(max_contacts), 100))

        q_parts = [f"newer_than:{lookback_days}d"]
        if query:
            # We intentionally do NOT push the query into Gmail search because we want
            # stable contact extraction; we filter locally by name/email.
            pass
        q = " ".join(q_parts)

        list_req = service.users().messages().list(userId="me", q=q, maxResults=max_messages)
        resp = await _execute_google_request(list_req)
        message_ids = [m.get("id") for m in (resp.get("messages") or []) if m.get("id")]

        contacts: dict[str, dict[str, Any]] = {}

        def _is_no_reply(addr: str) -> bool:
            a = (addr or "").lower()
            return ("noreply" in a) or ("no-reply" in a)

        scanned = 0
        partial = False

        for mid in message_ids:
            # Hard stop to avoid long tool calls that can make the UI feel frozen.
            if (time.monotonic() - started) > time_budget_s:
                partial = True
                break

            get_req = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=mid,
                    format="metadata",
                    metadataHeaders=["From", "To", "Cc", "Date"],
                )
            )
            msg = await _execute_google_request(get_req)
            scanned += 1
            label_ids = msg.get("labelIds") or []
            is_sent = "SENT" in label_ids

            internal_ms = msg.get("internalDate")
            try:
                internal_dt = datetime.fromtimestamp(int(internal_ms) / 1000.0, tz=timezone.utc).astimezone(tz) if internal_ms else None
            except Exception:
                internal_dt = None

            headers = {h.get("name"): h.get("value") for h in ((msg.get("payload") or {}).get("headers") or [])}
            from_header = headers.get("From")
            to_header = headers.get("To")
            cc_header = headers.get("Cc")

            candidates: list[tuple[str, str]] = []
            if is_sent:
                candidates.extend(_extract_addresses(to_header))
                candidates.extend(_extract_addresses(cc_header))
            else:
                candidates.extend(_extract_addresses(from_header))

            for display_name, email in candidates:
                key = email.lower()
                entry = contacts.get(key)
                if not entry:
                    entry = {
                        "email": email,
                        "name": display_name or None,
                        "sentCount": 0,
                        "receivedCount": 0,
                        "totalCount": 0,
                        "lastSeen": internal_dt,
                        "isNoReply": _is_no_reply(email),
                    }
                    contacts[key] = entry

                # Prefer a non-empty name if we find one later
                if display_name and not entry.get("name"):
                    entry["name"] = display_name

                if is_sent:
                    entry["sentCount"] += 1
                else:
                    entry["receivedCount"] += 1
                entry["totalCount"] += 1

                if internal_dt:
                    prev = entry.get("lastSeen")
                    if not prev or internal_dt > prev:
                        entry["lastSeen"] = internal_dt

        # IMPORTANT:
        # Do NOT hard-filter by query. Users (and even ASR) misspell names.
        # Instead return the top contacts, and use query only as a ranking hint.
        qnorm = (query or "").strip().lower()
        rows_all = list(contacts.values())
        rows = rows_all

        def score_row(r: dict[str, Any]) -> float:
            """
            Fuzzy score used only for ranking (NOT filtering).
            Designed to handle:
              - misspelled first names (Igor vs Egor)
              - strong last-name matches (Borisov)
              - domain hints (acme)
            """
            if not qnorm:
                return 0.0

            name = str(r.get("name") or "").strip().lower()
            email = str(r.get("email") or "").strip().lower()
            hay = f"{name} {email}".strip()
            if not hay:
                return 0.0

            # Baseline fuzzy similarity against full "name email"
            score = difflib.SequenceMatcher(a=qnorm, b=hay).ratio()

            # Token-based boosts: if query tokens appear in name/email, bump score.
            tokens = [t for t in re.split(r"[^a-z0-9]+", qnorm) if t]
            if tokens:
                hits = 0
                for t in tokens:
                    if t in name or t in email:
                        hits += 1
                if hits:
                    score += 0.10 * hits

                # Strong boost for last-name match (common pattern: "First Last")
                last = tokens[-1]
                if last and last in name:
                    score += 0.35

            # Clamp
            if score > 1.0:
                score = 1.0
            return score

        rows.sort(
            key=lambda r: (
                score_row(r),
                r.get("totalCount", 0),
                r.get("lastSeen") or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )

        message_out: str | None = None
        if exclude_no_reply:
            rows = [r for r in rows if not r.get("isNoReply")]
            # If filtering removed everything but we *did* find contacts, fall back so the user isn't stuck with 0.
            if not rows and rows_all:
                rows = rows_all
                message_out = "No contacts found excluding no-reply; showing no-reply addresses too."

        rows = rows[:max_contacts]

        out_contacts: list[GmailContact] = []
        for r in rows:
            last_seen = r.get("lastSeen")
            last_seen_iso = last_seen.isoformat() if last_seen else None
            out_contacts.append(
                GmailContact(
                    email=r["email"],
                    name=r.get("name"),
                    totalCount=int(r.get("totalCount", 0)),
                    sentCount=int(r.get("sentCount", 0)),
                    receivedCount=int(r.get("receivedCount", 0)),
                    lastSeenIso=last_seen_iso,
                )
            )

        if context:
            suffix = " (partial)" if partial else ""
            await context.info(
                f"Scanned {scanned} messages and found {len(out_contacts)} contacts{suffix}. Query hint: {query or '(none)'}."
            )

        return ListGmailContactsResult(
            status="success",
            lookbackDays=lookback_days,
            maxMessagesScanned=scanned,
            query=query,
            contacts=out_contacts,
            message=(
                "Partial results due to time budget." if partial else message_out
            ),
        ).model_dump()

    except Exception as exc:  # pragma: no cover
        msg = f"Failed to list Gmail contacts: {exc}"
        if context:
            await context.error(msg)
        return ListGmailContactsResult(
            status="error",
            lookbackDays=int(lookback_days or 0),
            maxMessagesScanned=0,
            query=query,
            contacts=[],
            message=msg,
        ).model_dump()


