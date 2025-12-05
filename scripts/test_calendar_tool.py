"""Utility script to trigger the calendar MCP tool once for manual testing."""

from __future__ import annotations

import asyncio
import json

from fastmcp.client import Client
from fastmcp.client.transports import StdioTransport


async def main() -> None:
    payload = {
        "summary": "Voice assistant test",
        "description": "quick sanity check",
        "start_iso": "2025-01-05T15:00:00-05:00",
        "duration_minutes": 30,
    }

    transport = StdioTransport(command="python", args=["src/workflow_server.py"])

    async with Client(transport=transport) as client:
        result = await client.call_tool("create_google_calendar_event", payload)

        structured = getattr(result, "structured_content", None)
        if structured is not None:
            print(json.dumps(structured, indent=2))
            return

        blocks = getattr(result, "content", []) or []
        text = "\n".join(getattr(block, "text", "") for block in blocks if getattr(block, "text", ""))
        print(text or result)


if __name__ == "__main__":
    asyncio.run(main())

