"""Workflow tool registry."""

from __future__ import annotations

from .calendar import (
    create_google_calendar_event_tool,
    list_google_calendar_events_tool,
    delete_google_calendar_event_tool,
    update_google_calendar_event_tool,
)
from .gmail import (
    get_gmail_profile_tool,
    list_gmail_emails_tool,
    mark_gmail_emails_read_tool,
    create_gmail_draft_tool,
    send_gmail_draft_tool,
    list_gmail_contacts_tool,
)
from .tasks import (
    list_task_lists_tool,
    list_tasks_tool,
    create_task_tool,
    complete_task_tool,
    update_task_tool,
    delete_task_tool,
)
from .weather import get_weather_tool

__all__ = [
    # Calendar
    "create_google_calendar_event_tool",
    "list_google_calendar_events_tool",
    "delete_google_calendar_event_tool",
    "update_google_calendar_event_tool",
    # Gmail
    "get_gmail_profile_tool",
    "list_gmail_emails_tool",
    "mark_gmail_emails_read_tool",
    "create_gmail_draft_tool",
    "send_gmail_draft_tool",
    "list_gmail_contacts_tool",
    # Tasks
    "list_task_lists_tool",
    "list_tasks_tool",
    "create_task_tool",
    "complete_task_tool",
    "update_task_tool",
    "delete_task_tool",
    # Weather
    "get_weather_tool",
]

