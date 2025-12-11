from __future__ import annotations

import base64
import os
import sys
import asyncio
from functools import lru_cache
from typing import Any, Dict, Optional

from flask import Flask, jsonify, request
from uvicorn.middleware.wsgi import WSGIMiddleware
import uvicorn
from openai import OpenAI

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from agent import ChatAgent  # noqa: E402
from app_config import get_openai_settings  # noqa: E402

app = Flask(__name__)
chat_agent = ChatAgent()


@lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    settings = get_openai_settings()
    return OpenAI(api_key=settings.api_key)


def _text_to_speech(text: str, voice: str = "alloy") -> bytes:
    """Convert text to speech using OpenAI TTS and return raw audio bytes."""
    client = _get_openai_client()
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
    )
    return response.content

DEFAULT_HOST = os.environ.get("CHAT_SERVER_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("CHAT_SERVER_PORT", "5050"))
DEFAULT_LOG_LEVEL = os.environ.get("CHAT_SERVER_LOG_LEVEL", "info")

# Expose an ASGI wrapper so uvicorn can serve the Flask app.
asgi_app = WSGIMiddleware(app)


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

        text = result.get("text") or ""
        response_data: Dict[str, Any] = {
            "text": text,
            "tool_calls": result.get("tool_calls"),
        }

        # Generate TTS audio if there's text to speak
        if text.strip():
            voice = payload.get("voice", "alloy")
            audio_bytes = _text_to_speech(text, voice=voice)
            response_data["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
            response_data["audio_format"] = "mp3"

        return jsonify(response_data)
    except Exception as exc:  # pragma: no cover - debug friendly
        app.logger.exception("Chat endpoint failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.get("/health")
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    uvicorn.run(
        asgi_app,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        log_level=DEFAULT_LOG_LEVEL,
    )