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

    session_messages = [turn.to_message() for turn in session.turns]
    historical_count = len(session_messages)
    context_len = len(context_prefix or [])

    result = await run_chat_with_mcp_tools(
        messages=session_messages,
        context_prefix=context_prefix,
    )

    assistant_msg = result["assistant_message"]

    conversation = result.get("conversation") or []
    new_messages_start = 1 + context_len + historical_count
    for message in conversation[new_messages_start:]:
        if message.get("role") == "system":
            continue
        session_store.append_turn(
            session,
            message.get("role", "assistant"),
            message.get("content"),
            tool_call_id=message.get("tool_call_id"),
            tool_calls=message.get("tool_calls"),
        )
    session_store.trim_history(session)

    return {
        "text": assistant_msg.content,
        "tool_calls": result["tool_calls"],
        "session": session,
    }

