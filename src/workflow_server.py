"""FastMCP server exposing Google Calendar helper tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import Context, FastMCP

from workflows import (
    create_google_calendar_event_tool,
    list_google_calendar_events_tool,
)


server = FastMCP(
    name="voice-assistant-calendar",
    instructions=(
        "Expose tools that let an LLM inspect the user's Google Calendar and create events."
    ),
)


@server.tool(description="Create an event in Google Calendar using the configured credentials.")
async def create_google_calendar_event(
    summary: Annotated[str, "Title that will show up in Google Calendar."],
    start_iso: Annotated[
        str,
        "Start time in ISO-8601 format (e.g. 2025-12-05T09:30:00-05:00)."
    ],
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
    description: Annotated[str, "Optional event description or agenda."] = "",
    context: Context | None = None,
):
    return await create_google_calendar_event_tool(
        summary=summary,
        description=description,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        calendar_id=calendar_id,
        timezone_name=timezone_name,
        context=context,
    )


@server.tool(description="Retrieve upcoming events from Google Calendar.")
async def list_google_calendar_events(
    calendar_id: Annotated[
        str | None,
        "Calendar ID to read from. Defaults to GOOGLE_CALENDAR_ID env var or 'primary'.",
    ] = None,
    time_min_iso: Annotated[
        str | None,
        "ISO timestamp for the earliest event start to return. Defaults to now.",
    ] = None,
    time_max_iso: Annotated[
        str | None,
        "ISO timestamp for the latest event start to return. Defaults to 7 days after time_min.",
    ] = None,
    max_results: Annotated[
        int,
        "Maximum number of events to return (1-50). Defaults to 10.",
    ] = 10,
    include_cancelled: Annotated[
        bool,
        "Whether to include cancelled events in the response.",
    ] = False,
    context: Context | None = None,
):
    return await list_google_calendar_events_tool(
        calendar_id=calendar_id,
        time_min_iso=time_min_iso,
        time_max_iso=time_max_iso,
        max_results=max_results,
        include_cancelled=include_cancelled,
        context=context,
    )


if __name__ == "__main__":
    server.run()

