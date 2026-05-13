"""Task CRUD routes: create, update, close, delete, rename, deps, check-status, smart-create."""

import json as _json

from fastapi import HTTPException

from common import agent as _agent
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

import app_state
from common.tasks import (
    get_task as core_get_task,
    create_task as core_create_task,
    update_task as core_update_task,
    close_task as core_close_task,
    delete_task as core_delete_task,
    check_status as core_check_status,
    rename_task as core_rename_task,
    add_dependency as core_add_dep,
    remove_dependency as core_remove_dep,
    append_history as core_append_history,
    list_history as core_list_history,
)


# -- Models --

class TaskCreate(BaseModel):
    id: str
    description: str
    type: str = "feature"
    group: str = ""
    status: str = "not_started"
    dependencies: list = []


class TaskUpdate(BaseModel):
    description: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    ticket: Optional[dict] = None
    type: Optional[str] = None
    group: Optional[str] = None
    priority: Optional[int] = None
    ticket_id: Optional[str] = None
    ticket_url: Optional[str] = None
    dependencies: Optional[list] = None
    # Natural-language follow-up reminders (strings only). See
    # EvaDB._validate_follow_ups for the schema.
    follow_ups: Optional[list] = None


class TaskClose(BaseModel):
    reason: str = ""


class TaskRename(BaseModel):
    new_id: str


class DepBody(BaseModel):
    depends_on: str


# -- Endpoints --

@app_state.app.get("/api/projects/{project_id}/tasks/{task_id}")
def get_task(project_id: str, task_id: str):
    """Get a single task with effective status and dependencies."""
    result = core_get_task(project_id, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@app_state.app.post("/api/projects/{project_id}/tasks", status_code=201)
def create_task(project_id: str, body: TaskCreate):
    """Create a new task in the given project."""
    try:
        return core_create_task(
            project_id, body.id,
            description=body.description, type=body.type,
            status=body.status, group=body.group,
            dependencies=body.dependencies or None,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="Project not found")
    except ValueError as e:
        msg = str(e)
        if "already exists" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=422, detail=msg)


@app_state.app.put("/api/projects/{project_id}/tasks/{task_id}")
def update_task(project_id: str, task_id: str, body: TaskUpdate):
    """Update task fields (description, status, notes, ticket, type, follow_ups, ...)."""
    updates = body.model_dump(exclude_none=True)
    try:
        result = core_update_task(project_id, task_id, **updates)
    except ValueError as e:
        # EvaDB._validate_status / _validate_follow_ups raise ValueError for
        # malformed input; surface as 422 rather than a 500 traceback.
        raise HTTPException(status_code=422, detail=str(e))
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/check-status")
def check_and_update_status(project_id: str, task_id: str):
    """Re-check task status based on ticket/PR state."""
    result = core_check_status(project_id, task_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/close")
def close_task(project_id: str, task_id: str, body: TaskClose):
    """Close a task: set status to closed and record reason in notes."""
    result = core_close_task(project_id, task_id, reason=body.reason)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return result


@app_state.app.delete("/api/projects/{project_id}/tasks/{task_id}", status_code=204)
def delete_task_route(project_id: str, task_id: str):
    """Delete a task. Tasks with tickets cannot be deleted."""
    try:
        if not core_delete_task(project_id, task_id):
            raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/deps", status_code=201)
def add_dependency(project_id: str, task_id: str, body: DepBody):
    """Add a dependency: task_id depends on body.depends_on."""
    try:
        core_add_dep(project_id, task_id, body.depends_on)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@app_state.app.delete("/api/projects/{project_id}/tasks/{task_id}/deps/{depends_on}", status_code=204)
def remove_dependency(project_id: str, task_id: str, depends_on: str):
    """Remove a dependency between two tasks."""
    core_remove_dep(project_id, task_id, depends_on)


class TaskHistoryAppend(BaseModel):
    text: str


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/history", status_code=201)
def append_task_history(project_id: str, task_id: str, body: TaskHistoryAppend):
    """Append one history entry (<= 100 chars) to a task's timeline."""
    try:
        return core_append_history(project_id, task_id, body.text)
    except ValueError as e:
        msg = str(e)
        code = 404 if "not found" in msg else 422
        raise HTTPException(status_code=code, detail=msg)


@app_state.app.get("/api/projects/{project_id}/tasks/{task_id}/history")
def get_task_history(project_id: str, task_id: str, limit: int = 50):
    """Return a task's recent history (newest first)."""
    return {"history": core_list_history(project_id, task_id, limit=limit)}


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/rename")
def rename_task(project_id: str, task_id: str, body: TaskRename):
    """Rename a task atomically."""
    try:
        result = core_rename_task(project_id, task_id, body.new_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


# -- Smart Create (AI-powered) --

class SmartCreateBody(BaseModel):
    context: str
    task_id: Optional[str] = None
    description: Optional[str] = None


_SMART_CREATE_NOTES_FALLBACK_LIMIT = 500


def _sse(payload: dict) -> str:
    """Render one SSE data line. The smart-create stream emits a
    handful of these per request -- centralising the wrapper avoids
    `f"data: {_json.dumps(...)}\\n\\n"` repetition that obscures the
    flow."""
    return f"data: {_json.dumps(payload)}\n\n"


def _build_smart_create_prompt(
    project_id: str, proj_info: dict, body: "SmartCreateBody",
) -> str:
    """Compose the haiku prompt: project header, optional user-supplied
    fields, the existing-task list (for duplicate detection), and the
    JSON spec the model must respond in."""
    existing = [
        {"id": t.get("task_id", ""),
         "ticket_id": t.get("ticket_id", ""),
         "description": (t.get("description", "") or "")[:80],
         "status": t.get("status", "")}
        for t in app_state._db.list_tasks(project_id)
    ]
    head = (
        "You are analyzing a task creation request for a project dashboard.\n"
        f"\nProject: {project_id} ({proj_info.get('name', '')})\n"
        f"JIRA system: {proj_info.get('jira', 'unknown')}\n"
        f"\nUser context:\n\"\"\"\n{body.context}\n\"\"\"\n"
    )
    if body.task_id:
        head += f"\nUser suggested task ID: {body.task_id}\n"
    if body.description:
        head += f"\nUser suggested description: {body.description}\n"

    # Compose the prefix block first so the f-string below can
    # embed it. Empty install -> "(none configured)"; the AI is
    # told to leave ticket_url null in that case.
    from pr_sync import _settings_ticket_url_prefixes
    prefixes = _settings_ticket_url_prefixes()
    if prefixes:
        prefixes_block = "\n".join(
            f"  {k}<id>: {v.rstrip('/')}/<id>"
            for k, v in sorted(prefixes.items())
        )
    else:
        prefixes_block = "  (none configured -- always leave ticket_url null)"

    spec = f"""
Existing tasks in this project (check for duplicates):
{_json.dumps(existing, indent=2)}

Respond with ONLY a JSON object (no other text):
{{
  "task_id": "lowercase-hyphenated-descriptive-id",
  "description": "clear one-line description",
  "type": "feature|bug|test|chore",
  "group": "group name or empty string",
  "status": "not_started|in_progress|in_review",
  "ticket_id": "TICKET-123 or null if none mentioned",
  "ticket_url": "full JIRA URL or null",
  "notes": "concise summary (3-8 lines) of the user context: WHY this work matters, KEY constraints / acceptance criteria, any links or file paths the user referenced. Preserve specific names, IDs, paths verbatim. Plain text, no markdown headings.",
  "dependencies": ["existing-task-id", ...],
  "duplicate_of": "existing-task-id if duplicate, else null",
  "duplicate_reason": "why it is a duplicate, else null"
}}

Rules:
- task_id: max 50 chars, lowercase, hyphens only
- If the context mentions a JIRA ticket (like ABC-12345, FOO-987), extract it as ticket_id
- ticket_url: build from the configured prefix map below; if no
  match, leave null and the UI will render the ticket id as plain text
- DUPLICATE CHECK: if any existing task has the same ticket_id, set duplicate_of and duplicate_reason
- dependencies: only reference task IDs from the existing tasks list
- notes: keep the original wording where it carries information; do NOT pad with platitudes or restate the description.

Ticket prefix -> URL base (configured via `jira.ticket_url_prefixes`):
{prefixes_block}
"""
    return head + spec


def _smart_create_notes(plan: dict, raw_context: str) -> str:
    """Pick the notes value: the AI's summary if present, else a
    truncated copy of the user's raw context so the original
    information isn't lost."""
    notes = (plan.get("notes") or "").strip()
    if notes:
        return notes
    raw = (raw_context or "").strip()
    if not raw:
        return ""
    if len(raw) <= _SMART_CREATE_NOTES_FALLBACK_LIMIT:
        return raw
    return raw[:_SMART_CREATE_NOTES_FALLBACK_LIMIT].rstrip() + " ..."


def _apply_smart_create_plan(project_id: str, plan: dict, raw_context: str):
    """Generator that performs the create + side effects, yielding one
    SSE event per step. Caller wraps in StreamingResponse.

    Direct calls into core/ instead of shelling out to eva-cli: the
    subprocess form fires `emit_event` inside the child process, so
    SSE subscribers in this server never see `task.created` and the
    graph/list don't auto-refresh.
    """
    if plan.get("duplicate_of"):
        dup_id = plan["duplicate_of"]
        dup_reason = plan.get("duplicate_reason", "")
        yield _sse({"text": (
            f"ERROR: Duplicate detected -- task '{dup_id}' already has this "
            f"ticket. {dup_reason}"
        )})
        yield _sse({"done": True})
        return

    task_id = (plan.get("task_id") or "").strip()
    if not task_id:
        yield _sse({"error": "AI did not generate a task ID"})
        return

    yield _sse({"text": f"Creating task: {task_id}"})
    try:
        core_create_task(
            project_id, task_id,
            description=plan.get("description", ""),
            type=plan.get("type", "feature"),
            status=plan.get("status", "not_started") or "not_started",
            group=plan.get("group", ""),
        )
    except KeyError:
        yield _sse({"error": "Project not found"})
        return
    except ValueError as e:
        yield _sse({"error": f"Failed to create task: {e}"})
        return
    yield _sse({"text": f"Created task {task_id}"})

    ticket_id = plan.get("ticket_id")
    if ticket_id:
        core_update_task(
            project_id, task_id,
            ticket_id=ticket_id,
            ticket_url=plan.get("ticket_url") or "",
        )
        yield _sse({"text": f"Set ticket: {ticket_id}"})

    notes = _smart_create_notes(plan, raw_context)
    if notes:
        core_update_task(project_id, task_id, notes=notes)
        yield _sse({"text": "Saved context as task notes"})

    for dep in plan.get("dependencies", []):
        try:
            core_add_dep(project_id, task_id, dep)
            yield _sse({"text": f"Added dependency: {dep}"})
        except ValueError:
            # Hallucinated dep; skip rather than fail the whole create.
            pass

    yield _sse({"text": f'Done! Task "{task_id}" created.'})
    yield _sse({"done": True})


@app_state.app.post("/api/projects/{project_id}/tasks/smart-create")
def smart_create_task(project_id: str, body: SmartCreateBody):
    """Create a task using AI analysis of natural language context.
    Streams progress as SSE; the heavy lifting lives in
    `_apply_smart_create_plan`."""
    proj_info = app_state._db.get_project(project_id)
    if not proj_info:
        raise HTTPException(status_code=404, detail="Project not found")

    prompt = _build_smart_create_prompt(project_id, proj_info, body)

    def stream():
        try:
            # Tools disabled by the adapter so haiku doesn't try to
            # fetch JIRA URLs (doubles call time on JSON-only work).
            plan = _agent.analyze(prompt, model="haiku", timeout=60)
            if plan is None:
                yield _sse({"error":
                            "AI analysis failed or timed out; try a "
                            "shorter context or retry"})
                return
            yield from _apply_smart_create_plan(project_id, plan, body.context)
        except Exception as e:
            yield _sse({"error": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream")
