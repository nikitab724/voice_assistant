"""Calendar-related workflow tools."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastmcp import Context

from calendar_client import create_event_payload, get_calendar_service
from app_config import MissingCredentialsError, get_google_calendar_settings


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

    result = {
        "id": created.get("id"),
        "htmlLink": created.get("htmlLink"),
        "calendarId": calendar_id,
        "summary": created.get("summary"),
        "status": created.get("status"),
        "hangoutLink": created.get("hangoutLink"),
    }

    return {k: v for k, v in result.items() if v is not None}


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
    events = []
    for item in items:
        event_payload = {
            "id": item.get("id"),
            "summary": item.get("summary"),
            "description": item.get("description"),
            "start": item.get("start"),
            "end": item.get("end"),
            "status": item.get("status"),
            "htmlLink": item.get("htmlLink"),
            "hangoutLink": item.get("hangoutLink"),
            "location": item.get("location"),
        }
        events.append({k: v for k, v in event_payload.items() if v is not None})

    if context:
        await context.info(f"Fetched {len(events)} events from calendar '{calendar_id}'.")

    return {
        "calendarId": calendar_id,
        "timeMin": window_start.isoformat(),
        "timeMax": window_end.isoformat(),
        "events": events,
    }

