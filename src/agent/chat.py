"""Minimal chat agent that routes through the MCP bridge."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from chat import session_store
from chat.mcp_bridge import run_chat_with_mcp_tools


class ChatAgent:
    """Small wrapper that maintains in-memory session state and calls the MCP bridge."""

    def __init__(self, *, context_prefix: Optional[Sequence[Dict[str, Any]]] = None) -> None:
        self.context_prefix = context_prefix

    async def respond(
        self,
        *,
        session_id: str,
        user_message: str,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Take one user turn, call the MCP bridge, and store history in memory."""
        session = session_store.get_session(session_id, user_id=user_id)
        session_store.append_turn(session, "user", user_message)
        session_store.trim_history(session)

        result = await run_chat_with_mcp_tools(
            messages=[{"role": t.role, "content": t.content} for t in session.turns],
            context_prefix=self.context_prefix,
        )

        assistant_msg = result["assistant_message"]
        session_store.append_turn(
            session,
            assistant_msg.role or "assistant",
            assistant_msg.content or "",
        )
        session_store.trim_history(session)

        return {
            "text": assistant_msg.content,
            "tool_calls": result["tool_calls"],
            "session": session,
        }

