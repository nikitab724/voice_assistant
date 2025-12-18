from __future__ import annotations

import base64
import json
import os
import sys
import asyncio
import re
from functools import lru_cache
from typing import Any, AsyncGenerator, Dict, Optional

from flask import Flask, Response, jsonify, request, stream_with_context
from uvicorn.middleware.wsgi import WSGIMiddleware
import uvicorn
from openai import OpenAI

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from agent import ChatAgent  # noqa: E402
from app_config import get_openai_settings  # noqa: E402
from calendar_client import set_google_access_token  # noqa: E402
from user_context import set_user_timezone  # noqa: E402
from chat.mcp_bridge import run_chat_with_mcp_tools_streaming  # noqa: E402
from chat.session_store import get_session, append_turn, ChatTurn  # noqa: E402

app = Flask(__name__)
chat_agent = ChatAgent()


@lru_cache(maxsize=1)
def _get_openai_client() -> OpenAI:
    settings = get_openai_settings()
    return OpenAI(api_key=settings.api_key)


def _text_to_speech(text: str, voice: str = "nova", speed: float = 1.1) -> bytes:
    """Convert text to speech using OpenAI TTS and return raw audio bytes."""
    client = _get_openai_client()
    response = client.audio.speech.create(
        model="tts-1",
        voice=voice,
        input=text,
        response_format="mp3",
        speed=speed,
    )
    return response.content


def _normalize_for_tts(text: str) -> str:
    """
    Convert display text (may include markdown / lists) into something that sounds natural when spoken.
    Uses a small LLM so the user can SEE formatted text while HEARING plain English.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    model = os.environ.get("OPENAI_SPEECH_NORMALIZER_MODEL", "gpt-4.1-mini")
    client = _get_openai_client()

    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0.2,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Rewrite the user's input into spoken, conversational English for TTS. "
                        "Remove all markdown/formatting (no headings, bullets, numbering, code fences). "
                        "Do not read punctuation like '*' or '#'. "
                        "Prefer short sentences. "
                        "If there are lists, read them naturally in one sentence using 'and/then'. "
                        "If there are times/dates, speak them naturally (e.g. 'January fifth', 'nine thirty AM'). "
                        "Return ONLY the spoken text."
                    ),
                },
                {"role": "user", "content": cleaned},
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        return out or cleaned
    except Exception:
        # Fallback: better to speak raw text than fail TTS entirely
        return cleaned


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)\s*$")
_HEADER_RE = re.compile(r"^\s*#{1,6}\s+(.*)\s*$")
_LIST_START_RE = re.compile(r"(^|\n)\s*(?:[-*•]|\d+[.)])\s+")


def _normalize_for_tts_fast(text: str) -> str:
    """
    Fast, local normalizer for streamed speech chunks (no extra LLM call).
    Removes markdown-ish formatting and list numbering/bullets so TTS sounds natural.
    """
    if not text:
        return ""
    s = text.strip()
    if not s:
        return ""

    # Remove code fences entirely
    s = s.replace("```", "")

    lines = [ln.strip() for ln in s.splitlines() if ln.strip()]
    items: list[str] = []
    out_lines: list[str] = []

    for ln in lines:
        # Strip markdown headers
        m = _HEADER_RE.match(ln)
        if m:
            ln = m.group(1).strip()

        # Collect list items
        m2 = _LIST_ITEM_RE.match(ln)
        if m2:
            items.append(m2.group(1).strip())
            continue

        # Non-list line flushes any pending items
        if items:
            if len(items) == 1:
                out_lines.append(items[0])
            else:
                out_lines.append(", then ".join(items))
            items = []

        # Remove lightweight markdown emphasis markers
        ln = ln.replace("**", "").replace("*", "").replace("_", "")
        out_lines.append(ln)

    if items:
        if len(items) == 1:
            out_lines.append(items[0])
        else:
            out_lines.append(", then ".join(items))

    # Collapse whitespace
    spoken = " ".join(out_lines)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken


def _extract_speak_segments(buffer: str) -> tuple[list[str], str]:
    """
    Split streaming text into speakable segments.
    - Buffers list blocks (1., 2., -, *) and emits them as a single segment once the list ends.
    - Otherwise emits at paragraph boundaries or sentence boundaries, with a minimum size to reduce TTS calls.
    """
    if not buffer:
        return [], ""

    segments: list[str] = []
    remainder = buffer

    # State carried in the buffer itself: we only split on completed newlines/sentences.
    # We'll process complete lines first to detect list blocks.
    lines = remainder.split("\n")
    if len(lines) == 1:
        return [], buffer  # no complete line yet

    complete_lines = lines[:-1]
    tail = lines[-1]

    current_text_lines: list[str] = []
    current_list_items: list[str] = []
    in_list = False

    def flush_text(force: bool = False) -> None:
        nonlocal current_text_lines
        if not current_text_lines:
            return
        joined = "\n".join(current_text_lines).strip()
        if not joined:
            current_text_lines = []
            return
        # Only flush text when it looks "complete enough" unless forced
        if force or len(joined) >= 160 or joined.endswith((".", "!", "?", ":")):
            segments.append(joined)
            current_text_lines = []

    def flush_list(force: bool = False) -> None:
        nonlocal current_list_items, in_list
        if not current_list_items:
            in_list = False
            return
        if force or len(current_list_items) >= 2:
            segments.append("\n".join([f"- {it}" for it in current_list_items]).strip())
            current_list_items = []
            in_list = False

    for ln in complete_lines:
        raw = ln.rstrip()
        if not raw.strip():
            # Blank line ends current blocks
            flush_list(force=True)
            flush_text(force=True)
            continue

        m = _LIST_ITEM_RE.match(raw)
        if m:
            # Start/continue list
            flush_text(force=True)
            in_list = True
            current_list_items.append(m.group(1).strip())
            continue

        # Non-list line
        if in_list:
            # List ended; emit list as a whole chunk
            flush_list(force=True)
        current_text_lines.append(raw)
        # If this line ends a sentence, consider flushing
        if raw.strip().endswith((".", "!", "?")):
            flush_text(force=False)

    # After consuming complete lines, keep tail + any unflushed blocks as remainder
    # Rebuild remainder: any still-pending blocks + tail
    pending_parts: list[str] = []
    if in_list and current_list_items:
        pending_parts.append("\n".join([f"{i+1}. {it}" for i, it in enumerate(current_list_items)]))
    if current_text_lines:
        pending_parts.append("\n".join(current_text_lines))
    pending_parts.append(tail)
    new_remainder = "\n".join([p for p in pending_parts if p is not None])
    return segments, new_remainder


def _has_list_start(buffer: str) -> bool:
    return bool(_LIST_START_RE.search(buffer or ""))


def _should_normalize_for_tts(chunk: str) -> bool:
    s = (chunk or "").strip()
    if not s:
        return False
    if any(ch.isdigit() for ch in s):
        return True
    markdown_markers = ("```", "`", "#", "*", "|", "[", "]", "(", ")", "_")
    if any(m in s for m in markdown_markers):
        return True
    if s.startswith("- ") or s.startswith("* "):
        return True
    # Simple numbered list prefix like "1. " or "2) "
    if len(s) >= 3 and s[0].isdigit() and s[1] in {".", ")"} and s[2] == " ":
        return True
    return False


def _extract_complete_chunks(buffer: str) -> tuple[list[str], str]:
    """
    Extract speakable chunks from a streaming text buffer.
    A chunk ends at newline or sentence-ending punctuation (.!?), including optional trailing quotes/brackets.
    """
    chunks: list[str] = []
    if not buffer:
        return chunks, ""

    i = 0
    start = 0
    trailing = set("\"')]}")  # common trailing punctuation after end-of-sentence

    while i < len(buffer):
        ch = buffer[i]
        if ch == "\n":
            chunk = buffer[start:i].strip()
            if chunk:
                chunks.append(chunk)
            start = i + 1
        elif ch in ".!?":
            end = i + 1
            while end < len(buffer) and buffer[end] in trailing:
                end += 1
            chunk = buffer[start:end].strip()
            if chunk:
                chunks.append(chunk)
            start = end
        i += 1

    remainder = buffer[start:]
    return chunks, remainder

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
    # If present, allowed_tool_tags can be [] meaning "no tools allowed"
    allowed_tool_tags = payload.get("allowed_tool_tags", None)
    timezone_name = payload.get("timezone_name") or None
    
    # Set Google access token if provided (from iOS app)
    google_token = payload.get("google_access_token")
    if google_token:
        set_google_access_token(google_token)
        app.logger.info("Using provided Google access token for user %s", user_id)
    else:
        set_google_access_token(None)  # Clear any previous token

    # Set per-request timezone for downstream tools (Gmail/Calendar defaults & formatting)
    set_user_timezone(timezone_name)
    
    return asyncio.run(
        chat_agent.respond(
            session_id=session_id,
            user_message=message,
            user_id=user_id,
            allowed_tool_tags=allowed_tool_tags,
            timezone_name=timezone_name,
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
            voice = payload.get("voice", "nova")
            speech_text = _normalize_for_tts(text)
            audio_bytes = _text_to_speech(speech_text, voice=voice)
            response_data["audio"] = base64.b64encode(audio_bytes).decode("utf-8")
            response_data["audio_format"] = "mp3"

        return jsonify(response_data)
    except Exception as exc:  # pragma: no cover - debug friendly
        app.logger.exception("Chat endpoint failed: %s", exc)
        return jsonify({"error": str(exc)}), 400


@app.post("/api/chat/stream")
def chat_stream_endpoint():
    """
    Streaming chat endpoint using Server-Sent Events (SSE).
    Sends events:
      - event: text_delta, data: {"text": "..."}
      - event: tool_call, data: {"name": "...", "arguments": {...}}
      - event: tool_result, data: {"name": "...", "result": "..."}
      - event: audio, data: {"audio": "<base64>", "format": "mp3"}
      - event: done, data: {"full_text": "..."}
      - event: error, data: {"message": "..."}
    """
    try:
        payload = request.get_json(force=True) or {}
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    session_id = payload.get("session_id")
    message = payload.get("message")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400
    if not message:
        return jsonify({"error": "message is required"}), 400

    user_id = payload.get("user_id")
    google_token = payload.get("google_access_token")
    voice = payload.get("voice", "nova")
    # If present, allowed_tool_tags can be [] meaning "no tools allowed"
    allowed_tool_tags = payload.get("allowed_tool_tags", None)
    timezone_name = payload.get("timezone_name") or None

    def generate():
        # Send an immediate SSE comment to flush headers/bytes quickly (prevents client timeouts)
        yield ": open\n\n"

        # Set Google access token
        if google_token:
            set_google_access_token(google_token)
        else:
            set_google_access_token(None)

        # Set per-request timezone for downstream tools (Gmail/Calendar defaults & formatting)
        set_user_timezone(timezone_name)

        # Get session and build messages
        session = get_session(session_id, user_id=user_id)
        append_turn(session, role="user", content=message)
        
        history_messages = [turn.to_message() for turn in session.turns]
        
        full_text = ""
        tool_calls = []
        speech_buffer = ""

        async def run_stream():
            nonlocal full_text, tool_calls, speech_buffer
            async for event in run_chat_with_mcp_tools_streaming(
                history_messages,
                allowed_tags=allowed_tool_tags,
                timezone_name=timezone_name,
            ):
                event_type = event.get("type", "")
                event_data = event.get("data", {})

                if event_type == "text_delta":
                    chunk = event_data if isinstance(event_data, str) else ""
                    if chunk:
                        speech_buffer += chunk
                    yield f"event: text_delta\ndata: {json.dumps({'text': chunk})}\n\n"

                    # Stream audio only for "safe" segments (buffers list blocks until complete)
                    segs, speech_buffer = _extract_speak_segments(speech_buffer)
                    for seg in segs:
                        spoken = _normalize_for_tts_fast(seg)
                        if not spoken:
                            continue
                        try:
                            audio_bytes = _text_to_speech(spoken, voice=voice)
                            audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                            app.logger.debug("TTS stream segment (lines): %s", spoken[:120])
                            yield f"event: audio\ndata: {json.dumps({'audio': audio_b64, 'format': 'mp3'})}\n\n"
                        except Exception as e:
                            app.logger.error(f"TTS chunk failed: {e}")

                    # If we didn't emit anything and we're not inside a list block yet,
                    # allow sentence-based chunking so audio starts earlier.
                    if not segs and speech_buffer and not _has_list_start(speech_buffer):
                        chunks, speech_buffer = _extract_complete_chunks(speech_buffer)
                        for seg in chunks:
                            # Avoid tiny fragments (often sound weird)
                            if len(seg.strip()) < 60:
                                # Put it back and wait for more context
                                speech_buffer = seg + speech_buffer
                                break
                            spoken = _normalize_for_tts_fast(seg)
                            if not spoken:
                                continue
                            try:
                                audio_bytes = _text_to_speech(spoken, voice=voice)
                                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                                app.logger.debug("TTS stream segment (sentence): %s", spoken[:120])
                                yield f"event: audio\ndata: {json.dumps({'audio': audio_b64, 'format': 'mp3'})}\n\n"
                            except Exception as e:
                                app.logger.error(f"TTS chunk failed: {e}")

                elif event_type == "tool_call_start":
                    yield f"event: tool_call\ndata: {json.dumps(event_data)}\n\n"

                elif event_type == "tool_call_result":
                    tool_calls.append(event_data)
                    yield f"event: tool_result\ndata: {json.dumps(event_data)}\n\n"

                elif event_type == "done":
                    full_text = event_data.get("full_text", "")
                    tool_calls = event_data.get("tool_calls", [])
                    # Flush any remaining buffered text as audio, but only once the response is complete.
                    if speech_buffer.strip():
                        # Best-effort: first try to flush via segmenter (handles list blocks cleanly),
                        # then force-speak anything that still remains (prevents dropping the tail).
                        segs, remainder = _extract_speak_segments(speech_buffer + "\n")
                        for seg in segs:
                            spoken = _normalize_for_tts_fast(seg)
                            if not spoken:
                                continue
                            try:
                                audio_bytes = _text_to_speech(spoken, voice=voice)
                                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                                yield f"event: audio\ndata: {json.dumps({'audio': audio_b64, 'format': 'mp3'})}\n\n"
                            except Exception as e:
                                app.logger.error(f"TTS final chunk failed: {e}")

                        remainder_spoken = _normalize_for_tts_fast((remainder or "").strip())
                        if remainder_spoken:
                            try:
                                audio_bytes = _text_to_speech(remainder_spoken, voice=voice)
                                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                                yield f"event: audio\ndata: {json.dumps({'audio': audio_b64, 'format': 'mp3'})}\n\n"
                            except Exception as e:
                                app.logger.error(f"TTS final remainder failed: {e}")

                        speech_buffer = ""

                elif event_type == "error":
                    yield f"event: error\ndata: {json.dumps(event_data)}\n\n"

        # Run the async generator synchronously.
        # IMPORTANT: we must aggressively clean up (cancel pending tasks + shutdown asyncgens)
        # or Python will warn about destroyed-but-pending tasks when the client disconnects early.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async_gen = run_stream()
        try:
            while True:
                try:
                    # Send periodic keep-alives while waiting for the next event
                    event = loop.run_until_complete(
                        asyncio.wait_for(async_gen.__anext__(), timeout=10)
                    )
                    yield event
                except asyncio.TimeoutError:
                    # Keep-alive comment; safe to ignore by clients
                    yield ": keep-alive\n\n"
                except StopAsyncIteration:
                    break
        finally:
            # 1) Close the async generator (best effort)
            try:
                loop.run_until_complete(async_gen.aclose())
            except Exception:
                pass

            # 2) Cancel and drain any remaining tasks created inside this loop
            try:
                pending = asyncio.all_tasks(loop)  # type: ignore[arg-type]
            except TypeError:
                # Fallback for older signatures
                pending = asyncio.all_tasks()
            except Exception:
                pending = set()

            if pending:
                for task in pending:
                    task.cancel()
                try:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                except Exception:
                    pass

            # 3) Ensure any async generators (e.g. OpenAI stream iterators) are finalized
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass

            # 4) Detach loop from this thread and close it
            try:
                asyncio.set_event_loop(None)
            except Exception:
                pass
            loop.close()

        # Store assistant response in session
        if full_text:
            append_turn(session, role="assistant", content=full_text)

        # Final done event
        yield f"event: done\ndata: {json.dumps({'full_text': full_text, 'tool_calls': tool_calls})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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