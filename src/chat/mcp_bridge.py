"""Simple MCP bridge modeled after the ai-cookbook stdio clients."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
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
    match tags:
        case str():
            return {tags}
        case Iterable():
            return {str(tag) for tag in tags}
    return set()


def _filter_tools(
    tools: Sequence[MCPTool],
    *,
    allowed_names: Optional[Sequence[str]] = None,
    required_tags: Optional[Iterable[str]] = None,
) -> List[MCPTool]:
    names = set(allowed_names or [])
    tag_set = set(required_tags or [])
    filtered: List[MCPTool] = []
    for tool in tools:
        if names and tool.name not in names:
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
    required_tags: Optional[Iterable[str]] = None,
    model: Optional[str] = None,
    openai_client: Optional[AsyncOpenAI] = None,
) -> Dict[str, Any]:
    """Single round-trip with the LLM, letting it call MCP tools if needed."""
    global _openai_client

    conversation: List[Dict[str, Any]] = []
    agent_settings = get_agent_settings()
    central_now = datetime.now(ZoneInfo("America/Chicago"))
    now_context = f"Current datetime (Central Time): {central_now.strftime('%A, %B %d %Y %I:%M:%S %p %Z')}."
    system_message = agent_settings.instructions or ""
    system_message = (
        system_message + " " + "When not asked, do not mention the current date/time unless it's relevant."
    ).strip()
    conversation.append(
        {
            "role": "system",
            "content": f"{system_message} {now_context}".strip(),
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
        tools = _filter_tools(
            tools_result,
            allowed_names=allowed_names,
            required_tags=required_tags,
        )
        openai_tools = _format_tools_for_openai(tools)
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

                result = await client.call_tool(call.function.name, arguments=args)
                payload_text = _stringify_tool_result(result)
                _debug_log(
                    f"tool.{call.function.name}.response",
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
                        "name": call.function.name,
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

