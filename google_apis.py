"""Utility helpers for managing Google OAuth credentials."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

# Ensure the `src` directory (where app modules live) is on sys.path.
CURRENT_DIR = Path(__file__).resolve().parent
SRC_DIR = CURRENT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app_config import MissingCredentialsError, get_google_calendar_settings
from calendar_client import SCOPES


def run_oauth_flow() -> None:
    """Run the local OAuth consent flow and persist the resulting token."""
    settings = get_google_calendar_settings()
    client_secret_path = settings.oauth_client_secret_file
    token_path = settings.oauth_token_file

    if not client_secret_path or not token_path:
        raise MissingCredentialsError(
            "Set `google.oauth_client_secret_file` and `google.oauth_token_file` in config.json "
            "or corresponding environment variables before running this command."
        )

    client_secret_path = Path(client_secret_path)
    token_path = Path(token_path)

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0)

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    print(f"[google] Saved OAuth token to {token_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google API helper utilities for the voice assistant."
    )
    parser.add_argument(
        "command",
        choices=["oauth"],
        help="Command to run. Use 'oauth' to generate or refresh token.json.",
    )
    args = parser.parse_args()

    if args.command == "oauth":
        run_oauth_flow()


if __name__ == "__main__":
    main()

