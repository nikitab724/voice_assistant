"""Centralized configuration helpers for the voice assistant services."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from openai import AsyncOpenAI
else:  # pragma: no cover
    AsyncOpenAI = Any  # type: ignore[assignment]


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_FILE = ROOT_DIR / "config.json"
_ENV_LOADED = False


class ConfigError(RuntimeError):
    """Base exception for configuration issues."""


class MissingSettingError(ConfigError):
    """Raised when a required environment variable is missing."""


class MissingCredentialsError(ConfigError):
    """Raised when credential material cannot be loaded."""


def _resolve_path(path_value: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "python-dotenv is required. Install it via `pip install python-dotenv`."
        ) from exc

    try:
        load_dotenv(dotenv_path=ROOT_DIR / ".env", override=False)
    except PermissionError:  # pragma: no cover - filesystem specific
        pass
    _ENV_LOADED = True


@cache
def _load_config_file() -> dict[str, Any]:
    config_path = Path(os.getenv("VOICE_ASSISTANT_CONFIG", DEFAULT_CONFIG_FILE))
    if not config_path.exists():
        return {}
    try:
        with config_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid user config
        raise ConfigError(f"Invalid JSON in {config_path}: {exc}") from exc


@dataclass(frozen=True)
class OpenAISettings:
    api_key: str
    default_model: str


@dataclass(frozen=True)
class AgentSettings:
    instructions: str | None = None


@cache
def get_openai_settings() -> OpenAISettings:
    """Load OpenAI configuration from config.json + env overrides."""
    _load_env_file()
    cfg = _load_config_file().get("openai", {})
    key_name = cfg.get("api_key_name", "OPENAI_API_KEY")

    api_key = cfg.get("api_key") or os.getenv(key_name)
    if not api_key:
        raise MissingSettingError(
            f"Set {key_name} or provide openai.api_key in config.json to run chat completions."
        )

    default_model = cfg.get("model") or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return OpenAISettings(api_key=api_key, default_model=default_model)


def get_async_openai_client() -> AsyncOpenAI:
    """Return a ready-to-use AsyncOpenAI client."""
    try:
        from openai import AsyncOpenAI
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise ConfigError(
            "Install the openai package to use the chatbot bridge, or supply your own AsyncOpenAI client."
        ) from exc

    settings = get_openai_settings()
    return AsyncOpenAI(api_key=settings.api_key)


@dataclass(frozen=True)
class GoogleCalendarSettings:
    calendar_id: str
    delegate: Optional[str]
    service_account_info: Optional[dict[str, Any]] = None
    oauth_client_secret_file: Optional[Path] = None
    oauth_token_file: Optional[Path] = None


@cache
def get_google_calendar_settings() -> GoogleCalendarSettings:
    """Load Google Calendar connection details from config.json + env overrides."""
    _load_env_file()
    cfg = _load_config_file().get("google", {})

    raw_json = cfg.get("service_account_json") or os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
    json_path = cfg.get("service_account_file") or os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")

    info: dict[str, Any] | None = None
    if raw_json:
        info = json.loads(raw_json)
    elif json_path:
        resolved = _resolve_path(json_path)
        with resolved.open("r", encoding="utf-8") as fh:
            info = json.load(fh)

    oauth_client_secret = cfg.get("oauth_client_secret_file") or os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET_FILE"
    )
    oauth_token_file = cfg.get("oauth_token_file") or os.getenv("GOOGLE_OAUTH_TOKEN_FILE")

    oauth_client_secret_path = (
        _resolve_path(oauth_client_secret) if oauth_client_secret else None
    )
    oauth_token_path = _resolve_path(oauth_token_file) if oauth_token_file else None

    if info is None and oauth_client_secret_path is None:
        raise MissingCredentialsError(
            "Provide Google service-account credentials or OAuth client information in config.json "
            "or environment variables."
        )

    calendar_id = cfg.get("calendar_id") or os.getenv("GOOGLE_CALENDAR_ID", "primary")
    delegate = cfg.get("delegate") or os.getenv("GOOGLE_CALENDAR_DELEGATE")
    return GoogleCalendarSettings(
        calendar_id=calendar_id,
        delegate=delegate,
        service_account_info=info,
        oauth_client_secret_file=oauth_client_secret_path,
        oauth_token_file=oauth_token_path,
    )


@cache
def get_agent_settings() -> AgentSettings:
    _load_env_file()
    cfg = _load_config_file().get("agent", {})
    instructions = cfg.get("instructions")
    return AgentSettings(instructions=instructions)


__all__ = [
    "ConfigError",
    "MissingSettingError",
    "MissingCredentialsError",
    "OpenAISettings",
    "AgentSettings",
    "GoogleCalendarSettings",
    "get_agent_settings",
    "get_openai_settings",
    "get_async_openai_client",
    "get_google_calendar_settings",
]

