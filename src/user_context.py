"""Per-request user context (thread-local) shared across tools."""

from __future__ import annotations

from threading import local
from typing import Optional

_thread_local = local()


def set_user_timezone(timezone_name: Optional[str]) -> None:
    _thread_local.timezone_name = timezone_name


def get_user_timezone() -> Optional[str]:
    return getattr(_thread_local, "timezone_name", None)


