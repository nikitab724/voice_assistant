"""FastMCP server exposing Google Calendar helper tools."""

from __future__ import annotations

from typing import Annotated

from fastmcp import Context, FastMCP

from workflows import (
    create_google_calendar_event_tool,
    list_google_calendar_events_tool,
    delete_google_calendar_event_tool,
    update_google_calendar_event_tool,
    get_gmail_profile_tool,
    list_gmail_emails_tool,
    mark_gmail_emails_read_tool,
    create_gmail_draft_tool,
    send_gmail_draft_tool,
    list_gmail_contacts_tool,
    list_task_lists_tool,
    list_tasks_tool,
    create_task_tool,
    complete_task_tool,
    update_task_tool,
    delete_task_tool,
    get_weather_tool,
)


server = FastMCP(
    name="voice-assistant-calendar",
    instructions=(
        "Expose tools that let an LLM inspect the user's Google Calendar, create events, and delete events when asked."
    ),
)


@server.tool(
    description="Create an event in Google Calendar. Supports one-time and recurring events (daily, weekly, monthly, yearly).",
    tags=["calendar"],
)
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
    recurrence_frequency: Annotated[
        str | None,
        "For recurring events: 'daily', 'weekly', 'monthly', or 'yearly'. Leave empty for one-time events.",
    ] = None,
    recurrence_interval: Annotated[
        int,
        "Repeat every N periods (e.g. 2 for every other week). Defaults to 1.",
    ] = 1,
    recurrence_count: Annotated[
        int | None,
        "Number of occurrences (e.g. 10 for 10 classes). Leave empty for indefinite.",
    ] = None,
    recurrence_until_iso: Annotated[
        str | None,
        "End date for recurrence in ISO-8601 format. Alternative to recurrence_count.",
    ] = None,
    recurrence_days: Annotated[
        list[str] | None,
        "For weekly recurrence: which days (e.g. ['MO', 'WE', 'FR'] for Mon/Wed/Fri).",
    ] = None,
    context: Context | None = None,
):
    return await create_google_calendar_event_tool(
        summary=summary,
        description=description,
        start_iso=start_iso,
        duration_minutes=duration_minutes,
        calendar_id=calendar_id,
        timezone_name=timezone_name,
        recurrence_frequency=recurrence_frequency,
        recurrence_interval=recurrence_interval,
        recurrence_count=recurrence_count,
        recurrence_until_iso=recurrence_until_iso,
        recurrence_days=recurrence_days,
        context=context,
    )


@server.tool(
    description="Retrieve events from Google Calendar. Can be filtered by time range or a search query (e.g. 'breakfast'). If searching by query, prefer a wider time range (e.g. +/- 7 days) to ensure you don't miss the event due to date ambiguities. Use a query to find specific event IDs before updating or deleting them.",
    tags=["calendar"],
)
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
    query: Annotated[
        str | None,
        "Optional text search query to filter events by summary or description.",
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
        query=query,
        max_results=max_results,
        include_cancelled=include_cancelled,
        context=context,
    )


@server.tool(
    description="Delete an event from Google Calendar. NEVER guess or use placeholder IDs. If you don't already have the correct event ID in your context, use 'list_google_calendar_events' (optionally with a query) to find it first.",
    tags=["calendar"],
)
async def delete_google_calendar_event(
    event_id: Annotated[str, "The identifier of the event to delete."],
    calendar_id: Annotated[
        str | None,
        "Calendar ID to delete from. Defaults to GOOGLE_CALENDAR_ID env var or 'primary'.",
    ] = None,
    delete_series: Annotated[
        bool,
        "For recurring events: True to delete ALL instances, False to delete just this one occurrence.",
    ] = False,
    context: Context | None = None,
):
    return await delete_google_calendar_event_tool(
        event_id=event_id,
        calendar_id=calendar_id,
        delete_series=delete_series,
        context=context,
    )


@server.tool(
    description="Update an existing Google Calendar event. NEVER guess or use placeholder IDs. If you don't already have the correct event ID in your context, use 'list_google_calendar_events' (optionally with a query) to find it first.",
    tags=["calendar"],
)
async def update_google_calendar_event(
    event_id: Annotated[str, "The identifier of the event to update."],
    summary: Annotated[
        str | None,
        "New title for the event. Leave empty to keep current title.",
    ] = None,
    description: Annotated[
        str | None,
        "New description for the event. Leave empty to keep current.",
    ] = None,
    start_iso: Annotated[
        str | None,
        "New start time in ISO-8601 format. Leave empty to keep current time.",
    ] = None,
    end_iso: Annotated[
        str | None,
        "New end time in ISO-8601 format. If start_iso is set without end_iso, duration_minutes is used.",
    ] = None,
    duration_minutes: Annotated[
        int | None,
        "Duration in minutes (used when start_iso is set but end_iso is not).",
    ] = None,
    location: Annotated[
        str | None,
        "New location for the event. Leave empty to keep current.",
    ] = None,
    calendar_id: Annotated[
        str | None,
        "Calendar ID. Defaults to GOOGLE_CALENDAR_ID env var or 'primary'.",
    ] = None,
    timezone_name: Annotated[
        str | None,
        "Timezone for start/end times (e.g. America/New_York).",
    ] = None,
    context: Context | None = None,
):
    return await update_google_calendar_event_tool(
        event_id=event_id,
        summary=summary,
        description=description,
        start_iso=start_iso,
        end_iso=end_iso,
        duration_minutes=duration_minutes,
        location=location,
        calendar_id=calendar_id,
        timezone_name=timezone_name,
        context=context,
    )


@server.tool(
    description="Check Gmail connectivity for the current user and return the Gmail profile (requires gmail.readonly scope).",
    tags=["gmail"],
)
async def get_gmail_profile(context: Context | None = None):
    return await get_gmail_profile_tool(context=context)


@server.tool(
    description="List Gmail emails within a time range. Defaults to the last 12 hours. Returns From/Subject/Date/snippet.",
    tags=["gmail"],
)
async def list_gmail_emails(
    start_iso: Annotated[
        str | None,
        "Start time in ISO-8601. Defaults to 12 hours ago if omitted.",
    ] = None,
    end_iso: Annotated[
        str | None,
        "End time in ISO-8601. Defaults to now if omitted.",
    ] = None,
    lookback_hours: Annotated[
        int | None,
        "Alternative to start_iso/end_iso: for queries like 'past N hours', set lookback_hours=N.",
    ] = None,
    max_results: Annotated[
        int,
        "Max emails to return (1-50). Defaults to 10.",
    ] = 10,
    query: Annotated[
        str | None,
        "Optional Gmail search query to further filter results, keep empty if user has not specified otherwise (e.g. 'from:stripe' or 'subject:invoice').",
    ] = None,
    category: Annotated[
        str | None,
        "Inbox category filter: primary, promotions, social, updates, forums. Defaults to primary if user has not specified otherwise.",
    ] = "primary",
    unread_only: Annotated[
        bool,
        "If true, only return unread emails. Defaults to true if user has not specified otherwise.",
    ] = True,
    context: Context | None = None,
):
    return await list_gmail_emails_tool(
        start_iso=start_iso,
        end_iso=end_iso,
        lookback_hours=lookback_hours,
        max_results=max_results,
        query=query,
        category=category,
        unread_only=unread_only,
        context=context,
    )


@server.tool(
    description="Mark Gmail emails as read (removes the UNREAD label). Accepts message IDs; if a thread ID is provided, will attempt to mark the thread as read. Requires gmail.modify scope.",
    tags=["gmail"],
)
async def mark_gmail_emails_read(
    message_ids: Annotated[
        list[str],
        "List of Gmail message IDs to mark as read.",
    ],
    context: Context | None = None,
):
    return await mark_gmail_emails_read_tool(message_ids=message_ids, context=context)


@server.tool(
    description="Create a Gmail draft (does not send). If the user provides a person/name instead of an email address, first call list_gmail_contacts to find the most likely address and confirm it with the user. After creating the draft, instruct the user to review and tap the Send button in the app (do not attempt to send automatically).",
    tags=["gmail"],
)
async def create_gmail_draft(
    to: Annotated[str, "Recipient email address(es), comma-separated if multiple."],
    subject: Annotated[str, "Email subject line."],
    body: Annotated[str, "Email body text."],
    cc: Annotated[str | None, "Optional CC recipients, comma-separated."] = None,
    bcc: Annotated[str | None, "Optional BCC recipients, comma-separated."] = None,
    context: Context | None = None,
):
    return await create_gmail_draft_tool(
        to=to,
        subject=subject,
        body=body,
        cc=cc,
        bcc=bcc,
        context=context,
    )


@server.tool(
    description="Send an existing Gmail draft by draft_id. This is a sensitive action and should normally be triggered by explicit UI confirmation (Send button) rather than natural language.",
    tags=["gmail", "requires_confirmation"],
)
async def send_gmail_draft(
    draft_id: Annotated[str, "Gmail draft ID to send."],
    context: Context | None = None,
):
    return await send_gmail_draft_tool(draft_id=draft_id, context=context)


@server.tool(
    description="List suggested contacts (name/email) derived from your recent Gmail messages. Use this to resolve 'email John' into a real address. The optional query is only a ranking hint (misspellings are common), not a strict filter.",
    tags=["gmail"],
)
async def list_gmail_contacts(
    query: Annotated[str | None, "Optional name/email substring to filter (e.g. 'john' or 'acme.com')."] = None,
    lookback_days: Annotated[int, "How far back to scan in days. Defaults to 90."] = 90,
    max_messages: Annotated[int, "Max messages to scan (1-500). Defaults to 60."] = 60,
    max_contacts: Annotated[int, "Max contacts to return (1-100). Defaults to 50."] = 50,
    exclude_no_reply: Annotated[bool, "Exclude noreply/no-reply addresses. Defaults to true."] = True,
    context: Context | None = None,
):
    return await list_gmail_contacts_tool(
        query=query,
        lookback_days=lookback_days,
        max_messages=max_messages,
        max_contacts=max_contacts,
        exclude_no_reply=exclude_no_reply,
        context=context,
    )


# ---------------------------------------------------------------------------
# Google Tasks tools
# ---------------------------------------------------------------------------


@server.tool(
    description="List all task lists (e.g., 'My Tasks', 'Work', 'Shopping').",
    tags=["tasks"],
)
async def list_task_lists(
    context: Context | None = None,
):
    return await list_task_lists_tool(context=context)


@server.tool(
    description="List tasks from a task list. Shows pending tasks by default. Use this to check your todo list or see what needs to be done.",
    tags=["tasks"],
)
async def list_tasks(
    task_list_id: Annotated[str | None, "Task list ID. Defaults to primary task list if not specified."] = None,
    show_completed: Annotated[bool, "Whether to include completed tasks. Defaults to false."] = False,
    max_results: Annotated[int, "Maximum number of tasks to return. Defaults to 50."] = 50,
    context: Context | None = None,
):
    return await list_tasks_tool(
        task_list_id=task_list_id,
        show_completed=show_completed,
        max_results=max_results,
        context=context,
    )


@server.tool(
    description="Create a new task/todo item. Use for 'remind me to...', 'add to my todo list'. Only supports DATE (not time) - for time-specific reminders, use calendar instead.",
    tags=["tasks"],
)
async def create_task(
    title: Annotated[str, "The task title/description (e.g., 'Buy groceries', 'Call mom')."],
    notes: Annotated[str | None, "Optional additional notes or details for the task."] = None,
    due: Annotated[str | None, "Optional due DATE in YYYY-MM-DD format. Time is NOT supported - use calendar for timed reminders."] = None,
    task_list_id: Annotated[str | None, "Task list ID. Defaults to primary task list."] = None,
    context: Context | None = None,
):
    return await create_task_tool(
        title=title,
        notes=notes,
        due=due,
        task_list_id=task_list_id,
        context=context,
    )


@server.tool(
    description="Mark a task as completed. NEVER guess or use placeholder IDs. If you don't already have the correct task ID in your context, use 'list_tasks' to find it first.",
    tags=["tasks"],
)
async def complete_task(
    task_id: Annotated[str, "The task ID to mark as completed."],
    task_list_id: Annotated[str | None, "Task list ID. Defaults to primary task list."] = None,
    context: Context | None = None,
):
    return await complete_task_tool(
        task_id=task_id,
        task_list_id=task_list_id,
        context=context,
    )


@server.tool(
    description="Update an existing task's title, notes, or due date. NEVER guess or use placeholder IDs. If you don't already have the correct task ID in your context, use 'list_tasks' to find it first.",
    tags=["tasks"],
)
async def update_task(
    task_id: Annotated[str, "The task ID to update."],
    title: Annotated[str | None, "New title for the task."] = None,
    notes: Annotated[str | None, "New notes for the task."] = None,
    due: Annotated[str | None, "New due DATE in YYYY-MM-DD format (time not supported)."] = None,
    task_list_id: Annotated[str | None, "Task list ID. Defaults to primary task list."] = None,
    context: Context | None = None,
):
    return await update_task_tool(
        task_id=task_id,
        title=title,
        notes=notes,
        due=due,
        task_list_id=task_list_id,
        context=context,
    )


@server.tool(
    description="Delete a task. NEVER guess or use placeholder IDs. If you don't already have the correct task ID in your context, use 'list_tasks' to find it first.",
    tags=["tasks"],
)
async def delete_task(
    task_id: Annotated[str, "The task ID to delete."],
    task_list_id: Annotated[str | None, "Task list ID. Defaults to primary task list."] = None,
    context: Context | None = None,
):
    return await delete_task_tool(
        task_id=task_id,
        task_list_id=task_list_id,
        context=context,
    )


# ---------------------------------------------------------------------------
# Weather tools
# ---------------------------------------------------------------------------


@server.tool(
    description=(
        "Get weather for a location. Weather lookup ALWAYS uses latitude/longitude coordinates. "
        "If a city/place name is provided, the server will geocode it into coordinates first. "
        "If no location is provided, the server uses the user's iPhone-provided coordinates (device location). "
        "Can get current weather, a 5-day forecast, or weather for a specific date "
        "(past or future up to 16 days). "
        "If the user mentions a specific day/time (e.g. 'tomorrow', 'next Friday', 'on Dec 25'), pass datetime_iso "
        "(preferred) or date (YYYY-MM-DD). "
        "IMPORTANT: If you already have latitude/longitude from a previous weather result, reuse those coordinates "
        "for follow-up questions (e.g. 'next two days') instead of re-geocoding a location string."
    ),
    tags=["weather"],
)
async def get_weather(
    location: Annotated[str | None, "City name or place (e.g., 'Chicago', 'New York, NY', 'Paris, France')."] = None,
    latitude: Annotated[float | None, "Latitude coordinate (use with longitude instead of location name)."] = None,
    longitude: Annotated[float | None, "Longitude coordinate (use with latitude instead of location name)."] = None,
    datetime_iso: Annotated[str | None, "Optional ISO-8601 datetime (e.g. 2025-12-25T15:30:00-06:00). Date portion is used for daily weather."] = None,
    date: Annotated[str | None, "Optional date in YYYY-MM-DD format to get weather for a specific day."] = None,
    include_forecast: Annotated[bool, "Include 5-day forecast (only if date is not provided). Defaults to true."] = True,
    context: Context | None = None,
):
    return await get_weather_tool(
        location=location,
        latitude=latitude,
        longitude=longitude,
        datetime_iso=datetime_iso,
        date=date,
        include_forecast=include_forecast,
        context=context,
    )


if __name__ == "__main__":
    server.run()

