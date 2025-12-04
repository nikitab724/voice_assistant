"""High-level chatbot facade that keeps minimal in-memory state."""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from . import session_store
from .mcp_bridge import run_chat_with_mcp_tools


async def respond(
    *,
    session_id: str,
    user_message: str,
    user_id: Optional[str] = None,
    context_prefix: Optional[Sequence[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Take one user turn, update memory, and return the assistant reply."""
    session = session_store.get_session(session_id, user_id=user_id)
    session_store.append_turn(session, "user", user_message)
    session_store.trim_history(session)

    result = await run_chat_with_mcp_tools(
        messages=[{"role": t.role, "content": t.content} for t in session.turns],
        context_prefix=context_prefix,
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

