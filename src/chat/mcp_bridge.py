"""Simple MCP bridge modeled after the ai-cookbook stdio clients."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Iterable as ABCIterable
from datetime import datetime, timezone
import re
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, TYPE_CHECKING

from fastmcp import Client as FastMCPClient
from mcp.types import Tool as MCPTool

from app_config import get_agent_settings, get_async_openai_client, get_openai_settings
from workflow_server import server as calendar_mcp_server

if TYPE_CHECKING:
    from openai import AsyncOpenAI
else:  # pragma: no cover
    AsyncOpenAI = Any  # type: ignore[assignment]

_openai_client: AsyncOpenAI | None = None
_DEBUG_ENABLED = os.getenv("MCP_BRIDGE_DEBUG") == "1"


def _debug_log(label: str, payload: Any) -> None:
    if not _DEBUG_ENABLED:
        return
    try:
        serialized = json.dumps(payload, indent=2, default=str)
    except TypeError:
        serialized = str(payload)
    print(f"[mcp_bridge][debug] {label}:\n{serialized}\n")


def _print_conversation(label: str, conversation: Sequence[Dict[str, Any]]) -> None:
    """Always print the conversation payload sent to the LLM for quick debugging."""
    try:
        serialized = json.dumps(conversation, indent=2, ensure_ascii=False, default=str)
    except TypeError:
        serialized = str(conversation)
    print(f"[mcp_bridge] {label}:\n{serialized}\n")


def _serialize_tool_calls(tool_calls: Optional[Sequence[Any]]) -> Optional[List[Dict[str, Any]]]:
    if not tool_calls:
        return None
    serialized: List[Dict[str, Any]] = []
    for call in tool_calls:
        function = getattr(call, "function", None)
        serialized.append(
            {
                "id": getattr(call, "id", None),
                "type": getattr(call, "type", None),
                "function": {
                    "name": getattr(function, "name", None) if function else None,
                    "arguments": getattr(function, "arguments", None) if function else None,
                },
            }
        )
    return serialized


def _tool_tags(tool: MCPTool) -> Set[str]:
    meta = getattr(tool, "meta", {}) or {}
    fastmcp_meta = meta.get("_fastmcp", {}) or {}
    tags = fastmcp_meta.get("tags") or []
    # NOTE: Don't use match/case with typing.Iterable; it isn't a runtime class in Py3.13.
    if isinstance(tags, str):
        return {tags}
    if isinstance(tags, ABCIterable):
        return {str(tag) for tag in tags}
    return set()


def _filter_tools(
    tools: Sequence[MCPTool],
    *,
    allowed_names: Optional[Sequence[str]] = None,
    allowed_tags: Optional[Iterable[str]] = None,
    required_tags: Optional[Iterable[str]] = None,
) -> List[MCPTool]:
    names = set(allowed_names or [])
    any_tags = {str(t) for t in (allowed_tags or [])}
    tag_set = set(required_tags or [])
    # If the caller explicitly provided allowed_tags (even empty), respect it.
    # Empty list means "no tools allowed".
    if allowed_tags is not None and not any_tags:
        return []
    filtered: List[MCPTool] = []
    for tool in tools:
        if names and tool.name not in names:
            continue
        if any_tags and not (_tool_tags(tool) & any_tags):
            continue
        if tag_set and not tag_set.issubset(_tool_tags(tool)):
            continue
        filtered.append(tool)
    return filtered


def _format_tools_for_openai(tools: Sequence[MCPTool]) -> List[Dict[str, Any]]:
    formatted: List[Dict[str, Any]] = []
    for tool in tools:
        formatted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": tool.inputSchema
                    or {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                },
            }
        )
    return formatted


def _tool_availability_message(
    *,
    all_tools: Sequence[MCPTool],
    enabled_tools: Sequence[MCPTool],
    allowed_tags: Optional[Iterable[str]],
) -> Dict[str, Any]:
    """
    Dynamic context that tells the model what tool categories are enabled/disabled right now.
    This prevents it from mentioning disabled capabilities based on earlier conversation history.
    """
    all_tags: Set[str] = set()
    for t in all_tools:
        all_tags |= _tool_tags(t)

    if allowed_tags is None:
        enabled_tags = set(all_tags)
    else:
        enabled_tags = {str(t) for t in allowed_tags}

    disabled_tags = sorted(all_tags - enabled_tags)
    enabled_tags_sorted = sorted(enabled_tags)

    enabled_tool_names = ", ".join([t.name for t in enabled_tools]) if enabled_tools else "(none)"

    return {
        "role": "developer",
        "content": (
            "Tool availability (from user settings):\n"
            f"- Enabled tool categories: {', '.join(enabled_tags_sorted) if enabled_tags_sorted else '(none)'}\n"
            f"- Disabled tool categories: {', '.join(disabled_tags) if disabled_tags else '(none)'}\n"
            f"- Tools you can use right now: {enabled_tool_names}\n"
            "Rules:\n"
            "- Treat disabled categories as unavailable. Do not claim you can perform actions from disabled categories.\n"
            "- If asked about a disabled capability, explicitly say it is disabled in settings and offer alternatives.\n"
            "- If earlier messages claimed more capabilities, consider that outdated and re-evaluate based on the current tool list.\n"
        ),
    }


_CONFIRM_RE = re.compile(
    r"\b("
    r"yes|yeah|yep|confirm|confirmed|approve|approved|ok|okay|go ahead|send it|do it"
    r")\b",
    re.IGNORECASE,
)


def _last_user_message_text(conversation: Sequence[Dict[str, Any]]) -> str:
    for msg in reversed(conversation):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _is_user_confirmation(text: str) -> bool:
    return bool(_CONFIRM_RE.search(text or ""))


def _stringify_tool_result(result: Any) -> str:
    if result is None:
        return ""
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        try:
            return json.dumps(structured)
        except TypeError:
            pass
    data = getattr(result, "data", None)
    if data is not None:
        try:
            return json.dumps(data)
        except TypeError:
            pass
    content = getattr(result, "content", None)
    if content:
        texts = [
            getattr(chunk, "text", "")
            for chunk in content
            if getattr(chunk, "text", "")
        ]
        if texts:
            return "\n".join(texts)
    return ""


async def run_chat_with_mcp_tools(
    messages: Sequence[Dict[str, Any]],
    *,
    context_prefix: Optional[Sequence[Dict[str, Any]]] = None,
    allowed_names: Optional[Sequence[str]] = None,
    allowed_tags: Optional[Iterable[str]] = None,
    required_tags: Optional[Iterable[str]] = None,
    timezone_name: Optional[str] = None,
    model: Optional[str] = None,
    openai_client: Optional[AsyncOpenAI] = None,
) -> Dict[str, Any]:
    """Single round-trip with the LLM, letting it call MCP tools if needed."""
    global _openai_client

    conversation: List[Dict[str, Any]] = []
    agent_settings = get_agent_settings()
    tz_name = timezone_name or "America/Chicago"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover
        tz_name = "America/Chicago"
        tz = ZoneInfo(tz_name)
    local_now = datetime.now(tz)
    now_context = f"Current datetime ({tz_name}): {local_now.strftime('%A, %B %d %Y %I:%M:%S %p %Z')}."
    system_message = agent_settings.instructions or ""
    system_message = (
        system_message + " " + "When not asked, do not mention the current date/time unless it's relevant."
    ).strip()
    conversation.append(
        {
            "role": "system",
            "content": f"{system_message} User timezone: {tz_name}. {now_context}".strip(),
        }
    )
    if context_prefix:
        conversation.extend(context_prefix)
    conversation.extend(messages)

    llm = openai_client or _openai_client or get_async_openai_client()
    if _openai_client is None and openai_client is None:
        _openai_client = llm
    model_name = model or get_openai_settings().default_model

    async with FastMCPClient(calendar_mcp_server) as client:
        tools_result = await client.list_tools()
        tool_tags_by_name: Dict[str, Set[str]] = {t.name: _tool_tags(t) for t in tools_result}
        tools = _filter_tools(
            tools_result,
            allowed_names=allowed_names,
            allowed_tags=allowed_tags,
            required_tags=required_tags,
        )
        openai_tools = _format_tools_for_openai(tools)
        conversation.append(
            _tool_availability_message(
                all_tools=tools_result,
                enabled_tools=tools,
                allowed_tags=allowed_tags,
            )
        )
        print("[mcp_bridge] Available tools for this request:")
        for tool in openai_tools or []:
            print(f"  - {tool['function']['name']}: {tool['function']['description']}")
            print(f"    params: {tool['function']['parameters']}")

        _debug_log("conversation.before_first_call", conversation)
        _print_conversation("conversation.before_first_call", conversation)

        tool_summaries: List[Dict[str, Any]] = []

        max_tool_loops = 15
        loop_count = 0
        final_msg = None

        while True:
            response = await llm.chat.completions.create(
                model=model_name,
                messages=conversation,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else "none",
            )
            assistant_msg = response.choices[0].message
            serialized_tool_calls = _serialize_tool_calls(
                getattr(assistant_msg, "tool_calls", None)
            )
            assistant_entry: Dict[str, Any] = {
                "role": assistant_msg.role,
                "content": assistant_msg.content,
            }
            if serialized_tool_calls:
                assistant_entry["tool_calls"] = serialized_tool_calls
            conversation.append(assistant_entry)

            if not assistant_msg.tool_calls:
                final_msg = assistant_msg
                break

            tool_success = False
            for call in assistant_msg.tool_calls:
                args: Dict[str, Any] = {}
                if getattr(call.function, "arguments", None):
                    try:
                        args = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                tool_name = call.function.name
                # Enforce confirmation for sensitive tools (tagged requires_confirmation)
                if "requires_confirmation" in tool_tags_by_name.get(tool_name, set()):
                    last_user = _last_user_message_text(conversation)
                    if not _is_user_confirmation(last_user):
                        payload_text = json.dumps(
                            {
                                "status": "error",
                                "message": "User confirmation required before executing this action. Ask the user to confirm, then call the tool again.",
                                "tool": tool_name,
                            }
                        )
                    else:
                        result = await client.call_tool(tool_name, arguments=args)
                        payload_text = _stringify_tool_result(result)
                else:
                    result = await client.call_tool(tool_name, arguments=args)
                    payload_text = _stringify_tool_result(result)
                _debug_log(
                    f"tool.{tool_name}.response",
                    {"args": args, "payload": payload_text},
                )
                conversation.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": payload_text,
                    }
                )

                tool_summaries.append(
                    {
                        "name": tool_name,
                        "arguments": args,
                        "response": payload_text,
                    }
                )

                try:
                    parsed = json.loads(payload_text) if payload_text else {}
                except json.JSONDecodeError:
                    parsed = {}
                status_value = str(parsed.get("status") or "").lower()
                if status_value == "success":
                    tool_success = True

            if tool_success:
                conversation.append(
                    {
                        "role": "system",
                        "content": "The tool call succeeded. Use its structured data to answer directly and do not say you lack access.",
                    }
                )
            else:
                conversation.append(
                    {
                        "role": "system",
                        "content": "The tool call failed or returned an error. Explain the issue using the tool output and offer next steps.",
                    }
                )

            _debug_log("conversation.before_followup", conversation)
            _print_conversation("conversation.before_followup", conversation)

            loop_count += 1
            if loop_count >= max_tool_loops:
                final_msg = SimpleNamespace(
                    role="assistant",
                    content="I reached the tool-call limit while trying to finish this request. Please try again or adjust the instructions.",
                )
                break

    _debug_log("assistant.final_message", final_msg.content)

    return {
        "assistant_message": final_msg,
        "tool_calls": tool_summaries,
        "conversation": conversation,
    }


def run_chat_with_mcp_tools_sync(*args, **kwargs) -> Dict[str, Any]:
    """Sync helper wrapping `run_chat_with_mcp_tools`."""
    return asyncio.run(run_chat_with_mcp_tools(*args, **kwargs))


async def run_chat_with_mcp_tools_streaming(
    messages: Sequence[Dict[str, Any]],
    *,
    context_prefix: Optional[Sequence[Dict[str, Any]]] = None,
    allowed_names: Optional[Sequence[str]] = None,
    allowed_tags: Optional[Iterable[str]] = None,
    required_tags: Optional[Iterable[str]] = None,
    timezone_name: Optional[str] = None,
    model: Optional[str] = None,
    openai_client: Optional[AsyncOpenAI] = None,
):
    """
    Streaming version of run_chat_with_mcp_tools.
    Yields events as dicts with 'type' and 'data' keys:
      - {"type": "text_delta", "data": "chunk of text"}
      - {"type": "tool_call_start", "data": {"name": "...", "arguments": {...}}}
      - {"type": "tool_call_result", "data": {"name": "...", "result": "..."}}
      - {"type": "done", "data": {"full_text": "...", "tool_calls": [...]}}
      - {"type": "error", "data": {"message": "..."}}
    """
    global _openai_client

    conversation: List[Dict[str, Any]] = []
    agent_settings = get_agent_settings()
    tz_name = timezone_name or "America/Chicago"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # pragma: no cover
        tz_name = "America/Chicago"
        tz = ZoneInfo(tz_name)
    local_now = datetime.now(tz)
    now_context = f"Current datetime ({tz_name}): {local_now.strftime('%A, %B %d %Y %I:%M:%S %p %Z')}."
    system_message = agent_settings.instructions or ""
    system_message = (
        system_message + " " + "When not asked, do not mention the current date/time unless it's relevant."
    ).strip()
    conversation.append(
        {
            "role": "system",
            "content": f"{system_message} User timezone: {tz_name}. {now_context}".strip(),
        }
    )
    if context_prefix:
        conversation.extend(context_prefix)
    conversation.extend(messages)

    llm = openai_client or _openai_client or get_async_openai_client()
    if _openai_client is None and openai_client is None:
        _openai_client = llm
    model_name = model or get_openai_settings().default_model

    async with FastMCPClient(calendar_mcp_server) as client:
        tools_result = await client.list_tools()
        tool_tags_by_name: Dict[str, Set[str]] = {t.name: _tool_tags(t) for t in tools_result}
        tools = _filter_tools(
            tools_result,
            allowed_names=allowed_names,
            allowed_tags=allowed_tags,
            required_tags=required_tags,
        )
        openai_tools = _format_tools_for_openai(tools)
        conversation.append(
            _tool_availability_message(
                all_tools=tools_result,
                enabled_tools=tools,
                allowed_tags=allowed_tags,
            )
        )

        tool_summaries: List[Dict[str, Any]] = []
        full_text = ""
        max_tool_loops = 15
        loop_count = 0

        while True:
            # Accumulate the streamed response
            current_content = ""
            current_tool_calls: Dict[int, Dict[str, Any]] = {}  # index -> tool call data

            # Use streaming for the LLM call with async context manager
            async with await llm.chat.completions.create(
                model=model_name,
                messages=conversation,
                tools=openai_tools or None,
                tool_choice="auto" if openai_tools else "none",
                stream=True,
            ) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if not delta:
                        continue

                    # Handle text content
                    if delta.content:
                        current_content += delta.content
                        full_text += delta.content
                        yield {"type": "text_delta", "data": delta.content}

                    # Handle tool calls (streamed in parts)
                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in current_tool_calls:
                                current_tool_calls[idx] = {
                                    "id": tc.id or "",
                                    "name": "",
                                    "arguments": "",
                                }
                            if tc.id:
                                current_tool_calls[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    current_tool_calls[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    current_tool_calls[idx]["arguments"] += tc.function.arguments

            # Build assistant message for conversation history
            assistant_entry: Dict[str, Any] = {
                "role": "assistant",
                "content": current_content or None,
            }

            # Convert accumulated tool calls
            tool_calls_list = []
            if current_tool_calls:
                for idx in sorted(current_tool_calls.keys()):
                    tc_data = current_tool_calls[idx]
                    tool_calls_list.append({
                        "id": tc_data["id"],
                        "type": "function",
                        "function": {
                            "name": tc_data["name"],
                            "arguments": tc_data["arguments"],
                        },
                    })
                assistant_entry["tool_calls"] = tool_calls_list

            conversation.append(assistant_entry)

            # If no tool calls, we're done
            if not tool_calls_list:
                break

            # Execute tool calls
            tool_success = False
            for tc in tool_calls_list:
                func_name = tc["function"]["name"]
                args_str = tc["function"]["arguments"]

                try:
                    args = json.loads(args_str) if args_str else {}
                except json.JSONDecodeError:
                    args = {}

                yield {"type": "tool_call_start", "data": {"name": func_name, "arguments": args}}

                # Enforce confirmation for sensitive tools (tagged requires_confirmation)
                if "requires_confirmation" in tool_tags_by_name.get(func_name, set()):
                    last_user = _last_user_message_text(conversation)
                    if not _is_user_confirmation(last_user):
                        payload_text = json.dumps(
                            {
                                "status": "error",
                                "message": "User confirmation required before executing this action. Ask the user to confirm, then call the tool again.",
                                "tool": func_name,
                            }
                        )
                    else:
                        result = await client.call_tool(func_name, arguments=args)
                        payload_text = _stringify_tool_result(result)
                else:
                    result = await client.call_tool(func_name, arguments=args)
                    payload_text = _stringify_tool_result(result)

                yield {"type": "tool_call_result", "data": {"name": func_name, "result": payload_text}}

                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": payload_text,
                })

                tool_summaries.append({
                    "name": func_name,
                    "arguments": args,
                    "response": payload_text,
                })

                try:
                    parsed = json.loads(payload_text) if payload_text else {}
                except json.JSONDecodeError:
                    parsed = {}
                if str(parsed.get("status") or "").lower() == "success":
                    tool_success = True

            # Add nudge
            if tool_success:
                conversation.append({
                    "role": "system",
                    "content": "The tool call succeeded. Use its structured data to answer directly and do not say you lack access.",
                })
            else:
                conversation.append({
                    "role": "system",
                    "content": "The tool call failed or returned an error. Explain the issue using the tool output and offer next steps.",
                })

            loop_count += 1
            if loop_count >= max_tool_loops:
                yield {"type": "error", "data": {"message": "Tool call limit reached"}}
                break

    yield {
        "type": "done",
        "data": {
            "full_text": full_text,
            "tool_calls": tool_summaries,
            "conversation": conversation,
        },
    }

