"""Google Tasks helper utilities."""

from __future__ import annotations

import os
from pathlib import Path

import google.auth.transport.requests
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build

from app_config import MissingCredentialsError
from calendar_client import get_google_access_token

# Scopes used for the optional local-dev token-file fallback.
# (When using an iOS access token, scopes are already embedded in the token grant.)
SCOPES = [
    "https://www.googleapis.com/auth/tasks",
]


def get_tasks_service():
    """
    Build and return an authenticated Google Tasks API client.

    Primary path: use the per-request Google access token set via set_google_access_token()
    (same thread-local used by calendar_client).

    Fallback: local dev token file via GOOGLE_OAUTH_TOKEN_FILE / config.json (if present).
    """
    access_token = get_google_access_token()
    if access_token:
        creds = OAuthCredentials(token=access_token)
        return build("tasks", "v1", credentials=creds, cache_discovery=False)

    # Optional local-dev fallback (only if env vars are set)
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN_FILE")
    if token_path:
        p = Path(token_path)
        if not p.exists():
            raise MissingCredentialsError(
                f"OAuth token not found at {p}. Provide google_access_token from iOS or set GOOGLE_OAUTH_TOKEN_FILE."
            )
        creds = OAuthCredentials.from_authorized_user_file(str(p), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
            p.write_text(creds.to_json())
        if not creds.valid:
            raise MissingCredentialsError(
                "OAuth credentials are invalid. Provide google_access_token from iOS or refresh local token."
            )
        return build("tasks", "v1", credentials=creds, cache_discovery=False)

    raise MissingCredentialsError(
        "No Google Tasks authentication available. Provide google_access_token from iOS with tasks scope."
    )
