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
)

__all__ = [
    "create_google_calendar_event_tool",
    "list_google_calendar_events_tool",
    "delete_google_calendar_event_tool",
    "update_google_calendar_event_tool",
    "get_gmail_profile_tool",
    "list_gmail_emails_tool",
    "mark_gmail_emails_read_tool",
    "create_gmail_draft_tool",
    "send_gmail_draft_tool",
]

