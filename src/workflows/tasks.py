"""Google Tasks workflow tools."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from fastmcp import Context
from pydantic import BaseModel

from tasks_client import get_tasks_service
from user_context import get_user_timezone


def _get_effective_tz() -> ZoneInfo:
    tz_name = get_user_timezone() or "America/Chicago"
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return ZoneInfo("America/Chicago")


async def _execute_google_request(req) -> Any:
    """
    googleapiclient's .execute() is blocking; run it off the event loop thread.
    """
    return await asyncio.to_thread(req.execute)


# ---------------------------------------------------------------------------
# Pydantic models for structured results
# ---------------------------------------------------------------------------


class TaskList(BaseModel):
    id: str
    title: str
    updated: Optional[str] = None


class ListTaskListsResult(BaseModel):
    status: Literal["success", "error"]
    taskLists: list[TaskList]
    message: Optional[str] = None


class Task(BaseModel):
    id: str
    title: str
    notes: Optional[str] = None
    due: Optional[str] = None  # RFC3339 date string
    status: Optional[str] = None  # "needsAction" or "completed"
    completed: Optional[str] = None  # RFC3339 timestamp when completed
    parent: Optional[str] = None  # Parent task ID for subtasks


class ListTasksResult(BaseModel):
    status: Literal["success", "error"]
    taskListId: str
    taskListTitle: Optional[str] = None
    tasks: list[Task]
    message: Optional[str] = None


class CreateTaskResult(BaseModel):
    status: Literal["success", "error"]
    taskId: Optional[str] = None
    taskListId: Optional[str] = None
    title: Optional[str] = None
    due: Optional[str] = None
    message: Optional[str] = None


class UpdateTaskResult(BaseModel):
    status: Literal["success", "error"]
    taskId: Optional[str] = None
    message: Optional[str] = None


class DeleteTaskResult(BaseModel):
    status: Literal["success", "error"]
    taskId: Optional[str] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def list_task_lists_tool(
    context: Context | None = None,
) -> dict[str, Any]:
    """
    List all task lists (e.g., "My Tasks", "Work", etc.).
    """
    try:
        service = get_tasks_service()
        req = service.tasklists().list(maxResults=100)
        resp = await _execute_google_request(req)

        items = resp.get("items") or []
        task_lists = [
            TaskList(
                id=item.get("id", ""),
                title=item.get("title", ""),
                updated=item.get("updated"),
            )
            for item in items
        ]

        if context:
            await context.info(f"Found {len(task_lists)} task list(s).")

        return ListTaskListsResult(status="success", taskLists=task_lists).model_dump()
    except Exception as exc:
        msg = f"Failed to list task lists: {exc}"
        if context:
            await context.error(msg)
        return ListTaskListsResult(status="error", taskLists=[], message=msg).model_dump()


async def list_tasks_tool(
    *,
    task_list_id: str | None = None,
    show_completed: bool = False,
    max_results: int = 50,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    List tasks from a task list. Defaults to primary task list if not specified.
    """
    try:
        service = get_tasks_service()
        
        # Use "@default" for the primary task list if not specified
        list_id = task_list_id or "@default"
        
        # Get task list info for title
        task_list_title = None
        if list_id != "@default":
            try:
                tl_req = service.tasklists().get(tasklist=list_id)
                tl_resp = await _execute_google_request(tl_req)
                task_list_title = tl_resp.get("title")
            except Exception:
                pass
        else:
            task_list_title = "My Tasks"

        req = service.tasks().list(
            tasklist=list_id,
            maxResults=max_results,
            showCompleted=show_completed,
            showHidden=show_completed,
        )
        resp = await _execute_google_request(req)

        items = resp.get("items") or []
        tasks = [
            Task(
                id=item.get("id", ""),
                title=item.get("title", ""),
                notes=item.get("notes"),
                due=item.get("due"),
                status=item.get("status"),
                completed=item.get("completed"),
                parent=item.get("parent"),
            )
            for item in items
        ]

        if context:
            await context.info(f"Found {len(tasks)} task(s) in '{task_list_title or list_id}'.")

        return ListTasksResult(
            status="success",
            taskListId=list_id,
            taskListTitle=task_list_title,
            tasks=tasks,
        ).model_dump()
    except Exception as exc:
        msg = f"Failed to list tasks: {exc}"
        if context:
            await context.error(msg)
        return ListTasksResult(
            status="error",
            taskListId=task_list_id or "@default",
            tasks=[],
            message=msg,
        ).model_dump()


def _parse_due_date(due: str) -> str | None:
    """
    Parse a due date string into RFC3339 format for Google Tasks API.
    NOTE: Google Tasks only supports DATE, not time - time is ignored by API.
    """
    from datetime import timedelta
    import re
    
    due_stripped = due.strip()
    due_lower = due_stripped.lower()
    tz = _get_effective_tz()
    today = datetime.now(tz).date()
    target = None
    
    # Handle relative dates
    if due_lower in ("today", "tonight"):
        target = today
    elif due_lower == "tomorrow":
        target = today + timedelta(days=1)
    elif due_lower == "next week":
        target = today + timedelta(weeks=1)
    elif due_lower in ("next month", "in a month"):
        target = today + timedelta(days=30)
    elif due_lower.startswith("in "):
        try:
            parts = due_lower.split()
            if len(parts) >= 3:
                num = int(parts[1])
                unit = parts[2].rstrip("s")
                if unit == "day":
                    target = today + timedelta(days=num)
                elif unit == "week":
                    target = today + timedelta(weeks=num)
                elif unit == "month":
                    target = today + timedelta(days=num * 30)
        except (ValueError, IndexError):
            pass
    
    if target:
        return f"{target.isoformat()}T00:00:00Z"
    
    # Strip any time component if present
    if "T" in due_stripped:
        due_stripped = due_stripped.split("T")[0]
    
    # YYYY-MM-DD format
    if len(due_stripped) == 10 and due_stripped[4] == "-" and due_stripped[7] == "-":
        return f"{due_stripped}T00:00:00Z"
    
    # MM/DD/YYYY format
    match = re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', due_stripped)
    if match:
        month, day, year = match.groups()
        return f"{year}-{month.zfill(2)}-{day.zfill(2)}T00:00:00Z"
    
    print(f"[tasks] Could not parse due date: '{due}'")
    return None


async def create_task_tool(
    *,
    title: str,
    notes: str | None = None,
    due: str | None = None,
    task_list_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Create a new task. Due date can be YYYY-MM-DD, ISO datetime, or relative like 'tomorrow'.
    """
    try:
        service = get_tasks_service()
        list_id = task_list_id or "@default"

        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            print(f"[tasks] Raw due date from LLM: '{due}'")
            parsed_due = _parse_due_date(due)
            print(f"[tasks] Parsed due date: '{parsed_due}'")
            if parsed_due:
                body["due"] = parsed_due
            elif context:
                await context.info(f"Could not parse due date '{due}', creating task without due date.")

        print(f"[tasks] Creating task with body: {body}")
        req = service.tasks().insert(tasklist=list_id, body=body)
        created = await _execute_google_request(req)

        task_id = created.get("id")
        created_title = created.get("title")
        created_due = created.get("due")

        if context:
            due_str = f" (due: {created_due[:10]})" if created_due else ""
            await context.info(f"Created task: '{created_title}'{due_str}")

        return CreateTaskResult(
            status="success",
            taskId=task_id,
            taskListId=list_id,
            title=created_title,
            due=created_due,
        ).model_dump()
    except Exception as exc:
        msg = f"Failed to create task: {exc}"
        if context:
            await context.error(msg)
        return CreateTaskResult(status="error", message=msg).model_dump()


async def complete_task_tool(
    *,
    task_id: str,
    task_list_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Mark a task as completed.
    """
    try:
        service = get_tasks_service()
        list_id = task_list_id or "@default"

        # First get the task to preserve other fields
        get_req = service.tasks().get(tasklist=list_id, task=task_id)
        task = await _execute_google_request(get_req)

        # Update status to completed
        task["status"] = "completed"
        update_req = service.tasks().update(tasklist=list_id, task=task_id, body=task)
        updated = await _execute_google_request(update_req)

        if context:
            await context.info(f"Marked task '{updated.get('title', task_id)}' as completed.")

        return UpdateTaskResult(
            status="success",
            taskId=task_id,
            message=f"Task '{updated.get('title')}' marked as completed.",
        ).model_dump()
    except Exception as exc:
        msg = f"Failed to complete task: {exc}"
        if context:
            await context.error(msg)
        return UpdateTaskResult(status="error", taskId=task_id, message=msg).model_dump()


async def update_task_tool(
    *,
    task_id: str,
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
    task_list_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Update an existing task's title, notes, or due date.
    """
    try:
        service = get_tasks_service()
        list_id = task_list_id or "@default"

        # Get current task
        get_req = service.tasks().get(tasklist=list_id, task=task_id)
        task = await _execute_google_request(get_req)

        # Update fields if provided
        if title is not None:
            task["title"] = title
        if notes is not None:
            task["notes"] = notes
        if due is not None:
            parsed_due = _parse_due_date(due)
            if parsed_due:
                task["due"] = parsed_due
            elif context:
                await context.info(f"Could not parse due date '{due}', skipping due date update.")

        update_req = service.tasks().update(tasklist=list_id, task=task_id, body=task)
        updated = await _execute_google_request(update_req)

        if context:
            await context.info(f"Updated task '{updated.get('title', task_id)}'.")

        return UpdateTaskResult(
            status="success",
            taskId=task_id,
            message=f"Task '{updated.get('title')}' updated.",
        ).model_dump()
    except Exception as exc:
        msg = f"Failed to update task: {exc}"
        if context:
            await context.error(msg)
        return UpdateTaskResult(status="error", taskId=task_id, message=msg).model_dump()


async def delete_task_tool(
    *,
    task_id: str,
    task_list_id: str | None = None,
    context: Context | None = None,
) -> dict[str, Any]:
    """
    Delete a task.
    """
    try:
        service = get_tasks_service()
        list_id = task_list_id or "@default"

        req = service.tasks().delete(tasklist=list_id, task=task_id)
        await _execute_google_request(req)

        if context:
            await context.info(f"Deleted task {task_id}.")

        return DeleteTaskResult(
            status="success",
            taskId=task_id,
            message="Task deleted.",
        ).model_dump()
    except Exception as exc:
        msg = f"Failed to delete task: {exc}"
        if context:
            await context.error(msg)
        return DeleteTaskResult(status="error", taskId=task_id, message=msg).model_dump()
