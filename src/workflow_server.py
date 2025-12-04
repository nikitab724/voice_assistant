"""FastMCP server exposing a single tool that creates Google Calendar events."""

from __future__ import annotations

import os
from typing import Annotated

from fastmcp import Context, FastMCP

from calendar_client import (
    MissingCredentialsError,
    create_event_payload,
    get_calendar_service,
)


server = FastMCP(
    name="voice-assistant-calendar",
    instructions=(
        "Expose a single tool that books Google Calendar events so an LLM can schedule "
        "meetings directly."
    ),
)


@server.tool(description="Create an event in Google Calendar using the configured credentials.")
async def create_google_calendar_event(
    summary: Annotated[str, "Title that will show up in Google Calendar."],
    description: Annotated[str, "Optional event description or agenda."] = "",
    start_iso: Annotated[
        str,
        "Start time in ISO-8601 format (e.g. 2025-12-05T09:30:00-05:00).",
    ] = "",
    duration_minutes: Annotated[
        int,
        "How long the event should last. Defaults to 60 minutes.",
    ] = 60,
    calendar_id: Annotated[
        str | None,
        "Calendar ID to insert into. Defaults to GOOGLE_CALENDAR_ID env var or 'primary'.",
    ] = None,
    timezone_name: Annotated[
        str | None,
        "Optional override for the event timezone (e.g. America/New_York).",
    ] = None,
    context: Context | None = None,
) -> dict[str, str | int | dict[str, object]]:
    if not summary:
        raise ValueError("summary is required")
    if not start_iso:
        raise ValueError("start_iso is required")

    calendar_id = calendar_id or os.getenv("GOOGLE_CALENDAR_ID", "primary")

    if context:
        await context.info(f"Creating event '{summary}' on calendar '{calendar_id}'.")

    event_payload = create_event_payload(
        summary=summary,
        description=description,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        timezone_name=timezone_name,
    )

    try:
        service = get_calendar_service()
    except MissingCredentialsError as exc:
        if context:
            await context.error(str(exc))
        raise

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

    return {
        "id": created.get("id"),
        "htmlLink": created.get("htmlLink"),
        "calendarId": calendar_id,
        "summary": created.get("summary"),
        "status": created.get("status"),
        "hangoutLink": created.get("hangoutLink"),
    }


if __name__ == "__main__":
    server.run()

