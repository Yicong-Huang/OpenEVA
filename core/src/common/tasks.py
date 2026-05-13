"""Task management core logic. Used by both server routes and CLI."""

import re as _re

import app_state

# -- Pure helpers (no DB access) --

def suggest_task_status(task, has_tickets=True):
    """Suggest what the status should be based on task data.
    Returns suggested status, or None if current status seems correct.

    `closed` is treated as a deliberate terminal state -- the user
    chose to abandon the task; we don't auto-flip it back to `done`
    even if a PR later appears merged (the close was intent, not
    drift). Every other status is game for promotion.
    """
    current = task.get("status", "not_started")
    if current == "closed":
        return None
    prs = task.get("prs", [])
    if prs:
        all_merged = all(p.get("status") == "merged" for p in prs)
        any_open = any(p.get("status") in ("open", "draft") for p in prs)
        if all_merged and current != "done":
            return "done"
        if any_open and current not in ("in_review", "done"):
            return "in_review"
    if has_tickets and task.get("ticket") and current == "not_started":
        return "in_progress"
    return None


def is_task_blocked(task_id, tasks):
    """Return True iff any dependency's status is NOT in
    `eva_db.UNBLOCKING_DEP_STATUSES` ({done, closed, needs_follow_up}).

    A missing dep (edge points to a task that doesn't exist in the
    map) is treated as blocking -- the data is in an inconsistent
    state and the dependent shouldn't be greenlit.
    """
    from eva_db import UNBLOCKING_DEP_STATUSES
    task = tasks.get(task_id, {})
    for dep_id in task.get("dependencies", []):
        dep = tasks.get(dep_id)
        if not dep:
            return True
        if dep.get("status") not in UNBLOCKING_DEP_STATUSES:
            return True
    return False


def _validate_task_id(task_id):
    """Validate task ID format. Raises ValueError on invalid IDs."""
    if not task_id or not _re.match(r'^[a-zA-Z0-9][a-zA-Z0-9._-]*$', task_id):
        raise ValueError("Invalid task ID: must be alphanumeric with .-_ only")
    if len(task_id) > 200:
        raise ValueError("Task ID too long (max 200 chars)")


def _fanout_dependents_status_changed(project_id: str, task_id: str) -> None:
    """When `task_id`'s status changes, every task that depends on it
    may have its computed `effective_status` (blocked vs not) flip.
    Emit a `task.updated` event for each direct dependent so the
    frontend's useProject subscriber picks up the new effective state
    without the user having to refresh.

    Only ONE level of fan-out -- if a chain A -> B -> C has its head
    flip, B's update_task call (triggered by the cascade) will fan out
    to C. We avoid recursing here so a long chain spreads via the
    natural event flow, not as a single-task synchronous walk.
    """
    try:
        dependents = app_state._db.list_dependents(project_id, task_id)
    except Exception:
        dependents = []
    for dep_task_id in dependents:
        _emit_task_event("task.updated", project_id, dep_task_id,
                         message="upstream dependency status changed",
                         persist=False)


def _emit_task_event(event_type: str, project_id: str, task_id: str,
                     *, title: str = "", message: str = "",
                     persist: bool = True) -> None:
    """Centralised task.* event emitter. Reduces 5 near-identical
    `app_state.emit_event("task.X", {...})` blocks across this module
    to one call each. `title` defaults to "Task <noun>: <task_id>"
    derived from the event_type's last segment."""
    if not title:
        noun = event_type.split(".")[-1].replace("_", " ").capitalize()
        title = f"Task {noun}: {task_id}"
    app_state.emit_event(event_type, {
        "title": title,
        "message": message,
        "session": project_id,
    }, persist=persist)


# Re-export the canonical state-machine allowlist from eva_db so callers
# can reason in terms of "tasks.VALID_TASK_STATUSES" (the semantic
# layer) rather than the storage module. Source of truth stays at the
# DB layer so the sqlite CHECK constraint and this Python allowlist
# literally share one set definition.
from eva_db import VALID_STATUSES as VALID_TASK_STATUSES  # noqa: F401


def _validate_status(status):
    """Raise ValueError when `status` isn't one of the canonical values.
    Empty / None passes (caller wanted to leave it unchanged). The DB
    layer runs its own guard on actual writes; this early check just
    fails fast before `load_tasks` and friends touch the DB."""
    if status in (None, ""):
        return
    if status not in VALID_TASK_STATUSES:
        valid = ", ".join(sorted(VALID_TASK_STATUSES))
        raise ValueError(
            f"Invalid task status: '{status}'. Valid values: {valid}"
        )


def derive_ticket_url(ticket_id: str) -> str:
    """Turn a bare ticket id (e.g. `EX-55390`) into the canonical
    browsing URL by looking up its prefix in the configured
    `jira.ticket_url_prefixes` setting.

    Returns "" for empty / malformed ids OR when no prefix mapping
    is configured. Callers (`update_task` / `create_task` /
    `derive_ticket_url` users) treat the empty return as "leave
    `ticket_url` blank" -- the UI then renders the ticket id as
    plain text instead of a link.

    Prefix mapping is fully settings-driven (no hardcoded vendors
    in core); operators populate it via `config.yaml`'s
    `jira.ticket_url_prefixes` or the in-app Settings UI. Common
    entries:
        EX: https://issues.example.org/jira/browse/
        FOO:   https://your-jira/browse/
    """
    tid = (ticket_id or "").strip()
    if not tid:
        return ""
    m = _re.match(r"^([A-Z][A-Z0-9_]*)-\d+$", tid)
    if not m:
        return ""
    prefix = m.group(1)
    from pr_sync import _settings_ticket_url_prefixes
    bases = _settings_ticket_url_prefixes()
    base = bases.get(prefix + "-") or bases.get(prefix)
    if not base:
        return ""
    return f"{base.rstrip('/')}/{tid}"


# -- Task CRUD --

def get_task(project_id, task_id):
    """Return single task dict with effective status, or None if not found."""
    tasks = app_state.load_tasks(project_id)
    task = tasks.get(task_id)
    if not task:
        return None
    status = task.get("status", "not_started")
    # `blocked` is a computed display status. Override for any task
    # whose stored status is NOT terminal -- a still-active task with
    # unclosed deps can't actually progress, so the UI should reflect
    # that. Terminal states (done, closed) stay as-is; the task is
    # over and its deps no longer matter.
    from eva_db import TERMINAL_TASK_STATUSES
    if status not in TERMINAL_TASK_STATUSES and is_task_blocked(task_id, tasks):
        status = "blocked"
    return {"id": task_id, **task, "effective_status": status}


# Aliases for `tasks.type` that should normalize to a canonical
# spelling at every write boundary. Without this, both `feat` and
# `feature` (or `doc`/`docs`, `bug`/`fix`) accumulate side by side and
# the badge UI splits one logical category into two -- the same bug
# the audit's `noncanonical_task_type` check fixes after the fact.
# Applying the map at create/update means audit findings stop
# regenerating after each cleanup pass.
TASK_TYPE_ALIASES: dict[str, str] = {
    "feat": "feature",
    "doc": "docs",
    "bug": "fix",
}


def canonicalize_task_type(t: str) -> str:
    """Map a task type to its canonical spelling. Pass-through for
    types that are already canonical or unrecognised (so OSS users who
    invent new categories aren't silently rewritten)."""
    if not isinstance(t, str):
        return t
    return TASK_TYPE_ALIASES.get(t, t)


def create_task(project_id, task_id, description="", type="feature",
                status="not_started", group="", dependencies=None):
    """Create a new task. Returns task dict.
    Raises ValueError if task_id is invalid or already exists.
    Raises KeyError if project not found.

    Tasks are always created blank (no ticket/notes/priority); use
    `update_task` afterwards for other fields. Direct DB-layer writes
    (e.g. pr_sync's auto-creation) go through `EvaDB.create_task`.
    """
    if not app_state._db.project_exists(project_id):
        raise KeyError("Project not found")
    _validate_task_id(task_id)
    _validate_status(status)
    existing = app_state._db.get_task(project_id, task_id)
    if existing:
        raise ValueError("Task ID already exists")
    type = canonicalize_task_type(type)
    task = app_state._db.create_task(
        project=project_id, task_id=task_id,
        description=description, type=type,
        status=status, group_name=group,
    )
    if dependencies:
        app_state._db.set_dependencies(project_id, task_id, dependencies)
        task = app_state._db.get_task(project_id, task_id)
    _emit_task_event("task.created", project_id, task_id,
                     message=description[:100])
    return {"id": task_id, **task}


def update_task(project_id, task_id, **fields):
    """Update task fields. Returns updated task dict, or None if not found.

    Raises ValueError when `status` is supplied but not in
    `VALID_TASK_STATUSES`. Checked BEFORE any DB read so a bad
    status never even touches persistence.
    """
    if "status" in fields:
        _validate_status(fields.get("status"))
    tasks = app_state.load_tasks(project_id)
    task = tasks.get(task_id)
    if not task:
        return None
    deps = fields.pop("dependencies", None)
    # Normalize nested {"ticket": {"id", "url"}} to explicit columns.
    ticket = fields.pop("ticket", None)
    if ticket is not None:
        if not isinstance(ticket, dict):
            raise ValueError("ticket must be an object with 'id' and 'url' keys")
        fields["ticket_id"] = ticket.get("id") or ""
        fields["ticket_url"] = ticket.get("url") or ""
    # If the caller explicitly set ticket_id / ticket_url (directly or via
    # the nested form above), drop the echoed-back ticket dict that
    # _task_row_to_dict inserts -- otherwise save_task's own translation
    # would overwrite the caller's intent with the stale DB value.
    if "ticket_id" in fields or "ticket_url" in fields:
        task.pop("ticket", None)
        # Auto-derive URL from a well-formed ticket_id when the caller
        # didn't supply one. Keeps the UI's "[TICKET] -> link" rendering
        # consistent and stops producing orphan-text tasks.
        tid_new = fields.get("ticket_id")
        url_new = fields.get("ticket_url")
        if tid_new and not url_new:
            derived = derive_ticket_url(tid_new)
            if derived:
                fields["ticket_url"] = derived
    # Normalise `group_name` (DB column form) to `group` (alias) so
    # the downstream save_task sees one canonical key. Without this,
    # CLI callers passing `group_name=...` would get silently dropped
    # because save_task's mapper only recognises `group`.
    if "group_name" in fields:
        fields["group"] = fields.pop("group_name")
    # Canonicalise type aliases at the write boundary so `feat`->`feature`
    # is enforced uniformly (CLI, route, internal callers all flow
    # through here).
    if "type" in fields and fields["type"] is not None:
        fields["type"] = canonicalize_task_type(fields["type"])
    old_status = task.get("status", "") or ""
    for key, val in fields.items():
        if val is not None:
            task[key] = val
    app_state.save_task(project_id, task_id, task)
    if deps is not None:
        app_state._db.set_dependencies(project_id, task_id, deps)
    result = app_state._db.get_task(project_id, task_id)
    # Auto-append a timeline entry when status flipped. Only fires when
    # the caller actually supplied a new status (fields["status"]) AND the
    # DB reflects the change -- guards against no-op writes that would
    # otherwise spam history with "status: X -> X".
    new_status = result.get("status", "") if result else ""
    if "status" in fields and fields.get("status") is not None:
        _record_status_transition(project_id, task_id, old_status, new_status)
    changed = [k for k in fields if k != "dependencies"]
    _emit_task_event("task.updated", project_id, task_id,
                     message=", ".join(changed) if changed else "dependencies")
    # Fan out to direct dependents iff the status actually flipped --
    # their `blocked` computed state may now read differently. Skip
    # when only non-status fields changed to avoid event spam.
    if old_status != new_status:
        _fanout_dependents_status_changed(project_id, task_id)
    return {"id": task_id, **result}


def close_task(project_id, task_id, reason=""):
    """Close task with reason. Returns task dict, or None if not found.

    Delegates to `update_task` so event emission, status-transition
    history, and dependent fanout all run through the single shared
    write path (server route + eva-cli + internal callers).
    """
    task = app_state._db.get_task(project_id, task_id)
    if not task:
        return None
    updates = {"status": "closed"}
    if reason:
        old_notes = task.get("notes", "") or ""
        close_note = "[Closed] %s" % reason
        updates["notes"] = ("%s\n%s" % (old_notes, close_note)).strip() if old_notes else close_note
    return update_task(project_id, task_id, **updates)


def delete_task(project_id, task_id):
    """Delete task. Returns True if deleted, False if not found.
    Raises ValueError if the task has a ticket (protected from deletion).

    Also deletes the session row keyed on this task_id so the All Live
    Tasks page doesn't surface orphan chips ("task missing") forever.
    The tmux session itself (if any) is left alone -- user can clean it
    up separately; killing tmux from a task-delete path feels unexpected."""
    task = app_state._db.get_task(project_id, task_id)
    if not task:
        return False
    if task.get("ticket_id"):
        raise ValueError(f"Cannot delete task '{task_id}': has ticket {task['ticket_id']}. Remove the ticket first.")
    deleted = app_state._db.delete_task(project_id, task_id)
    if deleted:
        app_state._db.delete_session(task_id)
        _emit_task_event("task.deleted", project_id, task_id)
    return deleted


def append_history(project_id: str, task_id: str, text: str) -> dict:
    """Append one line to a task's history timeline.

    Raises:
        ValueError: text missing / too long / task not found.
    Emits `task.history.appended` so the frontend can refetch / animate.
    Returns the inserted entry {ts, text}.
    """
    entry = app_state._db.append_task_history(project_id, task_id, text)
    _emit_task_event("task.history.appended", project_id, task_id,
                     title=f"History: {task_id}",
                     message=entry["text"], persist=False)
    return entry


def list_history(project_id: str, task_id: str, limit: int = 50) -> list:
    """Read a task's recent history entries (newest first)."""
    return app_state._db.list_task_history(project_id, task_id, limit=limit)


def _append_auto_history(project_id: str, task_id: str, text: str) -> None:
    """Write a state-machine-generated timeline entry.

    Best-effort: swallows DB exceptions so a history failure (truncation,
    concurrency) never breaks the real write that triggered it. The entry
    itself is capped at 100 chars by `append_task_history`; callers should
    keep messages short (<=90 chars leaves headroom).

    Scope: only called from state-transition sites (status change, PR
    linked, PR merged). Noisy per-field edits (notes, priority) do NOT
    auto-log -- the user pinned those concerns to `notes`, not the
    timeline. See `docs/loop-notes.md` iter 2026-04-24.
    """
    if not text:
        return
    try:
        app_state._db.append_task_history(project_id, task_id, text[:100])
    except Exception:
        pass


def _record_status_transition(project_id: str, task_id: str,
                              old_status: str, new_status: str) -> None:
    """Auto-log a task status X -> Y transition. No-op when equal or blank."""
    if not new_status or old_status == new_status:
        return
    _append_auto_history(project_id, task_id,
                         f"status: {old_status or 'none'} -> {new_status}")


def add_dependency(project_id, task_id, depends_on):
    """Add a dependency. Raises ValueError if either task doesn't exist."""
    if not app_state._db.get_task(project_id, task_id):
        raise ValueError(f"Task '{task_id}' not found")
    if not app_state._db.get_task(project_id, depends_on):
        raise ValueError(f"Dependency target '{depends_on}' not found")
    app_state._db.add_dependency(project_id, task_id, depends_on)
    # `task_id` may flip to blocked now that it has a new dep. Emit
    # so its TaskCard refreshes; no fan-out needed (deps' own status
    # didn't change, only this task's edge set did).
    _emit_task_event("task.updated", project_id, task_id,
                     message="dependency added", persist=False)


def remove_dependency(project_id, task_id, depends_on):
    """Remove a dependency."""
    app_state._db.remove_dependency(project_id, task_id, depends_on)
    # Removing an edge may unblock `task_id` (the user's example: "if
    # I delete the A->B edge, B should instantly unblock"). Same
    # fan-out story as add_dependency.
    _emit_task_event("task.updated", project_id, task_id,
                     message="dependency removed", persist=False)


def check_status(project_id, task_id):
    """Auto-detect and update status. Returns dict with changed, old_status,
    new_status, and task fields. Returns None if task not found."""
    proj = app_state._db.get_project(project_id) or {}
    has_tickets = proj.get("has_tickets", False)

    tasks = app_state.load_tasks(project_id)
    task = tasks.get(task_id)
    if not task:
        return None

    old_status = task.get("status", "not_started")
    suggested = suggest_task_status(task, has_tickets=has_tickets)
    blocked = is_task_blocked(task_id, tasks)

    if suggested and not (suggested == "in_progress" and blocked):
        task["status"] = suggested
        app_state.save_task(project_id, task_id, task)
        return {"id": task_id, "old_status": old_status, "new_status": suggested, "changed": True, **task}

    return {"id": task_id, "old_status": old_status, "new_status": old_status, "changed": False, **task}


def rename_task(project_id, old_id, new_id):
    """Rename task atomically. Returns new task dict, or None if source not
    found. Raises ValueError if target already exists."""
    if not app_state._db.get_task(project_id, old_id):
        return None
    success = app_state._db.rename_task(project_id, old_id, new_id)
    if not success:
        raise ValueError("Target task_id already exists")
    session = app_state._db.get_session(old_id)
    if session:
        app_state._db.delete_session(old_id)
        app_state._db.create_session(new_id, project_id)
    return app_state._db.get_task(project_id, new_id)
