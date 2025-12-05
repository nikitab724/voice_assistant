"""Google Calendar helper utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict

import google.auth.transport.requests
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.oauth2.service_account import Credentials as ServiceAccountCredentials
from googleapiclient.discovery import build

from app_config import MissingCredentialsError, get_google_calendar_settings

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_calendar_service():
    """Build and return an authenticated Calendar API client."""
    settings = get_google_calendar_settings()

    if settings.service_account_info:
        creds = ServiceAccountCredentials.from_service_account_info(
            settings.service_account_info,
            scopes=SCOPES,
        )
        if settings.delegate:
            creds = creds.with_subject(settings.delegate)
    elif settings.oauth_client_secret_file and settings.oauth_token_file:
        creds = _load_oauth_credentials(
            token_path=settings.oauth_token_file,
            client_secret_path=settings.oauth_client_secret_file,
        )
    else:
        raise MissingCredentialsError(
            "No Google Calendar authentication method configured. Provide either a service "
            "account or OAuth client secret/token file."
        )

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _load_oauth_credentials(
    *,
    token_path: Path,
    client_secret_path: Path,
):
    """Load and refresh OAuth credentials from disk."""
    if not client_secret_path.exists():
        raise MissingCredentialsError(
            f"OAuth client secret not found at {client_secret_path}. Update config.json or set "
            "GOOGLE_OAUTH_CLIENT_SECRET_FILE."
        )

    creds = None
    if token_path.exists():
        creds = OAuthCredentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds:
        raise MissingCredentialsError(
            f"OAuth token not found at {token_path}. Run `python google_apis.py oauth` to create it."
        )

    if creds.expired and creds.refresh_token:
        creds.refresh(google.auth.transport.requests.Request())
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    if not creds.valid:
        raise MissingCredentialsError(
            "OAuth credentials are invalid. Delete the token file and rerun `python google_apis.py oauth`."
        )

    return creds


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

