"""Google Calendar helper utilities."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class MissingCredentialsError(RuntimeError):
    """Raised when Google service-account credentials are missing."""


def _load_service_account_info() -> dict[str, Any]:
    raw_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    if raw_json:
        return json.loads(raw_json)

    if json_path:
        with open(json_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    raise MissingCredentialsError(
        "Set GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_SERVICE_ACCOUNT_FILE with the service account credentials."
    )


def get_calendar_service():
    """Build and return an authenticated Calendar API client."""
    info = _load_service_account_info()
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    delegated_user = os.getenv("GOOGLE_CALENDAR_DELEGATE")
    if delegated_user:
        creds = creds.with_subject(delegated_user)
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

