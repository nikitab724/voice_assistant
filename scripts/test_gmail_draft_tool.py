"""
Quick local test for create_gmail_draft_tool without hitting the real Gmail API.

It monkeypatches gmail.get_gmail_service() to return a fake service that captures the
payload passed into users().drafts().create(...).execute().
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from typing import Any, Dict, Optional


class _FakeDrafts:
    def __init__(self) -> None:
        self.last_user_id: Optional[str] = None
        self.last_body: Optional[Dict[str, Any]] = None

    def create(self, *, userId: str, body: Dict[str, Any]) -> "_FakeDrafts":
        self.last_user_id = userId
        self.last_body = body
        return self

    def execute(self) -> Dict[str, Any]:
        return {"id": "draft_123", "message": {"id": "msg_456"}}


class _FakeUsers:
    def __init__(self, drafts: _FakeDrafts) -> None:
        self._drafts = drafts

    def drafts(self) -> _FakeDrafts:
        return self._drafts


class _FakeGmailService:
    def __init__(self, drafts: _FakeDrafts) -> None:
        self._drafts = drafts

    def users(self) -> _FakeUsers:
        return _FakeUsers(self._drafts)


async def _run() -> None:
    # Mirror scripts/flask_server.py import behavior: add repo/src to sys.path
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    src_dir = os.path.join(root_dir, "src")
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from workflows import gmail as gmail_workflow

    fake_drafts = _FakeDrafts()
    fake_service = _FakeGmailService(fake_drafts)

    # Monkeypatch the imported symbol used inside gmail workflow
    gmail_workflow.get_gmail_service = lambda: fake_service  # type: ignore[assignment]

    result = await gmail_workflow.create_gmail_draft_tool(
        to="test@example.com",
        subject="Hello",
        body="Line 1\nLine 2",
        cc="cc@example.com",
        bcc=None,
        context=None,
    )

    assert result["status"] == "success", result
    assert result["draftId"] == "draft_123", result
    assert result["messageId"] == "msg_456", result

    assert fake_drafts.last_user_id == "me"
    assert fake_drafts.last_body is not None
    raw_b64 = fake_drafts.last_body["message"]["raw"]

    raw_bytes = base64.urlsafe_b64decode(raw_b64.encode("utf-8"))
    raw = raw_bytes.decode("utf-8", errors="replace")

    # Basic RFC822 sanity checks
    assert "To: test@example.com" in raw
    assert "Cc: cc@example.com" in raw
    assert "Subject: Hello" in raw
    assert re.search(r"^Date: .+$", raw, flags=re.MULTILINE), raw
    assert "Line 1" in raw and "Line 2" in raw

    print("OK: create_gmail_draft_tool produced a draft payload that looks correct.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(_run())


