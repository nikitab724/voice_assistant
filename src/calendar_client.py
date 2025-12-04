"""Google Calendar helper utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app_config import get_google_calendar_settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Build and return an authenticated Calendar API client."""
    settings = get_google_calendar_settings()
    creds = Credentials.from_service_account_info(
        settings.service_account_info,
        scopes=SCOPES,
    )
    if settings.delegate:
        creds = creds.with_subject(settings.delegate)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def create_event_payload(
    *,
    summary: str,
    description: str,
    start_iso: str,
    timezone_name: str | None = None,
    duration_minutes: int = 60,
) -> Dict[str, Any]:
    """Prepare a Google Calendar event payload."""
    start_dt = datetime.fromisoformat(start_iso)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    timezone_name = timezone_name or start_dt.tzname() or "UTC"
    end_dt = start_dt + timedelta(minutes=duration_minutes)

    def _format(dt: datetime) -> dict[str, str]:
        return {"dateTime": dt.isoformat(), "timeZone": timezone_name}

    return {
        "summary": summary,
        "description": description,
        "start": _format(start_dt),
        "end": _format(end_dt),
    }

