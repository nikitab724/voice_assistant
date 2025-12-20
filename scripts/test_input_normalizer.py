"""
Manual smoke-test for the lightweight input normalizer LLM.

This script prints original vs normalized text for a handful of realistic prompts
(emails, calendar checks, and common voice-to-text typos).
"""

from __future__ import annotations

import os
import sys
from typing import List, Tuple


TEST_CASES: List[Tuple[str, str]] = [
    (
        "Email (name only) + typos",
        "can you email jhon and tell him im runing 10 mins late. thx",
    ),
    (
        "Draft email (name only) + short",
        "draft an email to john about tomorows meeting at 9:30am, subject: agenda",
    ),
    (
        "Calendar check (typos)",
        "whats on my calender next wednesday after 2pm?",
    ),
    (
        "Create event with details",
        "add a meeting called product sync for 30 mins tommorow at 1pm ct with sarah and mike",
    ),
    (
        "Gmail query phrasing",
        "check my unred emails from the past 5 hours in primary and mark them as read",
    ),
    (
        "Email + name + domain hint",
        "send a note to sarah from acme about the invoice pls",
    ),
    (
        "Numbers/dates shouldn't change meaning",
        "remind me on 2025-12-21 at 09:05 to pay rent",
    ),
]


def main() -> None:
    # Import inside main so it runs from repo root and uses the same environment as the server.
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    scripts_dir = os.path.join(root_dir, "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    from flask_server import _normalize_user_input  # type: ignore

    print("=== Input normalizer smoke test ===")
    for title, text in TEST_CASES:
        normalized = _normalize_user_input(text)
        changed = "CHANGED" if normalized.strip() != text.strip() else "same"
        print(f"\n--- {title} [{changed}] ---")
        print("ORIG:", text)
        print("NORM:", normalized)


if __name__ == "__main__":
    main()


