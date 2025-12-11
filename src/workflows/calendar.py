"""Calendar-related workflow tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel

from fastmcp import Context

from calendar_client import create_event_payload, get_calendar_service
from app_config import MissingCredentialsError, get_google_calendar_settings


class CalendarEvent(BaseModel):
    id: Optional[str] = None
    summary: Optional[str] = None
    description: Optional[str] = None
    start: Optional[Dict[str, Any]] = None
    end: Optional[Dict[str, Any]] = None
    status: Optional[str] = None
    htmlLink: Optional[str] = None
    hangoutLink: Optional[str] = None
    location: Optional[str] = None
    calendarId: Optional[str] = None


class CreateEventResult(BaseModel):
    status: Literal["success", "error"]
    event: Optional[CalendarEvent] = None
    message: Optional[str] = None


class ListEventsResult(BaseModel):
    status: Literal["success", "error"]
    calendarId: str
    timeMin: str
    timeMax: str
    events: List[CalendarEvent]
    message: Optional[str] = None


class DeleteEventResult(BaseModel):
    status: Literal["success", "error"]
    eventId: Optional[str] = None
    message: Optional[str] = None


async def create_google_calendar_event_tool(
    summary: str,
    description: str = "",
    start_iso: str = "",
    duration_minutes: int = 60,
    calendar_id: str | None = None,
    timezone_name: str | None = None,
    context: Context | None = None,
) -> dict[str, str]:
    if not summary:
        raise ValueError("summary is required")
    if not start_iso:
        raise ValueError("start_iso is required")

    try:
        calendar_settings = get_google_calendar_settings()
    except MissingCredentialsError as exc:
        if context:
            await context.error(str(exc))
        raise

    calendar_id = calendar_id or calendar_settings.calendar_id

    if context:
        await context.info(f"Creating event '{summary}' on calendar '{calendar_id}'.")

    event_payload = create_event_payload(
        summary=summary,
        description=description,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        timezone_name=timezone_name,
    )

    service = get_calendar_service()

    created = (
        service.events()
        .insert(calendarId=calendar_id, body=event_payload, sendUpdates="all")
        .execute()
    )

    if context:
        html_link = created.get("htmlLink")
        await context.info(
            f"Google Calendar event created: {html_link or created.get('id')}"
        )

    event = CalendarEvent(
        id=created.get("id"),
        summary=created.get("summary"),
        description=created.get("description"),
        start=created.get("start"),
        end=created.get("end"),
        status=created.get("status"),
        htmlLink=created.get("htmlLink"),
        hangoutLink=created.get("hangoutLink"),
        location=created.get("location"),
        calendarId=calendar_id,
    )

    event_status = (created.get("status") or "").lower()
    status_value = "success" if event_status in {"confirmed", "tentative"} else "error"
    message = None
    if status_value == "error":
        message = f"Google Calendar returned status '{event_status or 'unknown'}' for the new event."

    result = CreateEventResult(
        status=status_value,
        event=event,
        message=message,
    )

    return result.model_dump()


def _coerce_datetime(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def list_google_calendar_events_tool(
    calendar_id: str | None = None,
    time_min_iso: str | None = None,
    time_max_iso: str | None = None,
    max_results: int = 10,
    include_cancelled: bool = False,
    context: Context | None = None,
) -> dict[str, Any]:
    if max_results <= 0:
        raise ValueError("max_results must be greater than zero")
    if max_results > 50:
        max_results = 50

    try:
        calendar_settings = get_google_calendar_settings()
    except MissingCredentialsError as exc:
        if context:
            await context.error(str(exc))
        raise

    calendar_id = calendar_id or calendar_settings.calendar_id

    window_start = _coerce_datetime(time_min_iso) if time_min_iso else datetime.now(timezone.utc)
    if time_max_iso:
        window_end = _coerce_datetime(time_max_iso)
    else:
        window_end = window_start + timedelta(days=7)

    if window_end <= window_start:
        raise ValueError("time_max_iso must be after time_min_iso")

    service = get_calendar_service()
    events_result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=window_start.isoformat(),
            timeMax=window_end.isoformat(),
            maxResults=max_results,
            singleEvents=True,
            orderBy="startTime",
            showDeleted=include_cancelled,
        )
        .execute()
    )

    items = events_result.get("items", [])
    events: List[CalendarEvent] = []
    for item in items:
        events.append(
            CalendarEvent(
                id=item.get("id"),
                summary=item.get("summary"),
                description=item.get("description"),
                start=item.get("start"),
                end=item.get("end"),
                status=item.get("status"),
                htmlLink=item.get("htmlLink"),
                hangoutLink=item.get("hangoutLink"),
                location=item.get("location"),
                calendarId=calendar_id,
            )
        )

    if context:
        await context.info(f"Fetched {len(events)} events from calendar '{calendar_id}'.")

    success_statuses = {"confirmed", "tentative"}
    invalid_events = [
        event.summary or event.id or "unknown event"
        for event in events
        if event.status and event.status.lower() not in success_statuses
    ]
    status_value = "success" if not invalid_events else "error"
    message = None
    if invalid_events:
        message = "Some events returned unexpected statuses: " + ", ".join(invalid_events)

    result = ListEventsResult(
        status=status_value,
        calendarId=calendar_id,
        timeMin=window_start.isoformat(),
        timeMax=window_end.isoformat(),
        events=events,
        message=message,
    )
    return result.model_dump()


async def delete_google_calendar_event_tool(
    event_id: str,
    calendar_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    if not event_id:
        raise ValueError("event_id is required")

    try:
        calendar_settings = get_google_calendar_settings()
    except MissingCredentialsError as exc:
        if context:
            await context.error(str(exc))
        raise

    calendar_id = calendar_id or calendar_settings.calendar_id

    if context:
        await context.info(f"Deleting event '{event_id}' from calendar '{calendar_id}'.")

    service = get_calendar_service()

    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        status_value = "success"
        message = None
        if context:
            await context.info("Event deleted successfully.")
    except Exception as exc:  # pragma: no cover - Google API errors
        status_value = "error"
        message = f"Failed to delete event: {exc}"
        if context:
            await context.error(message)

    result = DeleteEventResult(status=status_value, eventId=event_id, message=message)
    return result.model_dump()

