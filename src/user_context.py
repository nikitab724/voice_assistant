"""Per-request user context (thread-local) shared across tools."""

from __future__ import annotations

from threading import local
from typing import Optional, Tuple

_thread_local = local()


def set_user_timezone(timezone_name: Optional[str]) -> None:
    _thread_local.timezone_name = timezone_name


def get_user_timezone() -> Optional[str]:
    return getattr(_thread_local, "timezone_name", None)


def set_user_location(latitude: Optional[float], longitude: Optional[float]) -> None:
    _thread_local.user_latitude = latitude
    _thread_local.user_longitude = longitude


def get_user_location() -> Tuple[Optional[float], Optional[float]]:
    return (
        getattr(_thread_local, "user_latitude", None),
        getattr(_thread_local, "user_longitude", None),
    )
