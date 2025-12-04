"""Super-simple in-memory session store for prototyping."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ChatTurn:
    role: str
    content: str


@dataclass
class ChatSession:
    session_id: str
    user_id: str
    turns: List[ChatTurn] = field(default_factory=list)
    slots: Dict[str, str] = field(default_factory=dict)


_sessions: Dict[str, ChatSession] = {}


def get_session(session_id: str, *, user_id: Optional[str] = None) -> ChatSession:
    """Lookup or create a chat session."""
    session = _sessions.get(session_id)
    if session is None:
        session = ChatSession(session_id=session_id, user_id=user_id or session_id)
        _sessions[session_id] = session
    elif user_id and session.user_id != user_id:
        session.user_id = user_id
    return session


def append_turn(session: ChatSession, role: str, content: str) -> None:
    """Add a new message to the session history."""
    session.turns.append(ChatTurn(role=role, content=content))


def trim_history(session: ChatSession, *, max_turns: int = 20) -> None:
    """Keep only the most recent N turns to control token usage."""
    if len(session.turns) > max_turns:
        session.turns = session.turns[-max_turns:]


def reset_session(session_id: str) -> None:
    """Delete the session entirely."""
    _sessions.pop(session_id, None)

