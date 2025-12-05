from __future__ import annotations

import os
import sys
import asyncio
from typing import Any, Dict

from flask import Flask, jsonify, request

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from agent import ChatAgent  # noqa: E402

app = Flask(__name__)
chat_agent = ChatAgent()


def _run_agent_response(payload: Dict[str, Any]):
    session_id = payload.get("session_id")
    message = payload.get("message")
    if not session_id:
        raise ValueError("session_id is required")
    if not message:
        raise ValueError("message is required")
    user_id = payload.get("user_id")
    return asyncio.run(
        chat_agent.respond(
            session_id=session_id,
            user_message=message,
            user_id=user_id,
        )
    )


@app.post("/api/chat")
def chat_endpoint():
    try:
        payload = request.get_json(force=True) or {}
        result = _run_agent_response(payload)
        return jsonify(
            {
                "text": result.get("text"),
                "tool_calls": result.get("tool_calls"),
            }
        )
    except Exception as exc:  # pragma: no cover - debug friendly
        app.logger.exception("Chat endpoint failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(debug=True, port=5050)