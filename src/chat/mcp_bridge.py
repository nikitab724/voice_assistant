"""Helpers that let the chatbot call FastMCP tools via OpenAI-style tool calling."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, TYPE_CHECKING

from fastmcp import Client as FastMCPClient
from mcp.types import Tool as MCPTool

from app_config import get_async_openai_client, get_openai_settings
from workflow_server import server as calendar_mcp_server

if TYPE_CHECKING:
    from openai import AsyncOpenAI
else:  # pragma: no cover
    AsyncOpenAI = Any  # type: ignore[assignment]


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


def filter_tools(
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
        if tag_set:
            if not tag_set.issubset(_tool_tags(tool)):
                continue
        filtered.append(tool)
    return filtered


def format_tools_for_openai(tools: Sequence[MCPTool]) -> List[Dict[str, Any]]:
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


async def list_mcp_tools(
    *,
    allowed_names: Optional[Sequence[str]] = None,
    required_tags: Optional[Iterable[str]] = None,
) -> List[MCPTool]:
    async with FastMCPClient(calendar_mcp_server) as client:
        tools = await client.list_tools()
    return filter_tools(tools, allowed_names=allowed_names, required_tags=required_tags)


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
            if getattr(chunk, "text", "")  # type: ignore[arg-type]
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
    convo: List[Dict[str, Any]] = []
    if context_prefix:
        convo.extend(context_prefix)
    convo.extend(messages)

    llm = openai_client or get_async_openai_client()
    model_name = model or get_openai_settings().default_model

    async with FastMCPClient(calendar_mcp_server) as client:
        tools = filter_tools(
            await client.list_tools(),
            allowed_names=allowed_names,
            required_tags=required_tags,
        )
        openai_tools = format_tools_for_openai(tools)

        first_pass = await llm.chat.completions.create(
            model=model_name,
            messages=convo,
            tools=openai_tools or None,
            tool_choice="auto" if openai_tools else "none",
        )
        assistant_msg = first_pass.choices[0].message
        convo.append(
            {
                "role": assistant_msg.role,
                "content": assistant_msg.content,
                "tool_calls": getattr(assistant_msg, "tool_calls", None),
            }
        )

        tool_summaries: List[Dict[str, Any]] = []

        if assistant_msg.tool_calls:
            for call in assistant_msg.tool_calls:
                args: Dict[str, Any] = {}
                if getattr(call.function, "arguments", None):
                    try:
                        args = json.loads(call.function.arguments)
                    except json.JSONDecodeError:
                        args = {}

                result = await client.call_tool(call.function.name, args)
                payload_text = _stringify_tool_result(result)

                convo.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.function.name,
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

            follow_up = await llm.chat.completions.create(
                model=model_name,
                messages=convo,
                tools=openai_tools or None,
                tool_choice="none",
            )
            final_msg = follow_up.choices[0].message
        else:
            final_msg = assistant_msg

    return {
        "assistant_message": final_msg,
        "tool_calls": tool_summaries,
        "conversation": convo,
    }


def run_chat_with_mcp_tools_sync(*args, **kwargs) -> Dict[str, Any]:
    """Sync helper wrapping `run_chat_with_mcp_tools`."""
    return asyncio.run(run_chat_with_mcp_tools(*args, **kwargs))


__all__ = [
    "filter_tools",
    "format_tools_for_openai",
    "list_mcp_tools",
    "run_chat_with_mcp_tools",
    "run_chat_with_mcp_tools_sync",
]

