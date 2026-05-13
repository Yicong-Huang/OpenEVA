"""Session management core logic. Direct tmux + config_db access."""

import app_state
from adapters.tmux import session_exists, launch_session_argv, graceful_kill_session


def _enrich(s: dict) -> dict:
    """Attach `running` (tmux liveness) + `status` (from the unified
    `session_state` cache) onto a session row. One spot so the
    list / list_all / status helpers can't drift on what "live" means.
    """
    from . import session_state
    tmux_name = s["tmux_name"]
    cache_row = session_state.get(tmux_name) or {}
    return {
        **s,
        "running": session_exists(tmux_name),
        "status": cache_row.get("state", ""),
    }


def list_sessions(project=None):
    """List all sessions, optionally filtered by project. Returns list of dicts."""
    sessions = app_state._db.list_sessions(project=project)
    return [_enrich(s) for s in sessions]


def list_all_sessions():
    """List all sessions grouped by project. Returns dict of {project_id: {name, sessions}}."""
    all_sessions = app_state._db.list_sessions()
    result = {}
    for s in all_sessions:
        pid = s["project"]
        if pid not in result:
            proj = app_state._db.get_project(pid)
            proj_name = proj.get("name", pid) if proj else pid
            result[pid] = {"name": proj_name, "sessions": []}
        result[pid]["sessions"].append(_enrich(s))
    return result


def open_session(task_id, project_id, action_id="open", custom_prompt=None,
                 pr_number=None, pr_repo=None):
    """Open or resume an agent session for a task. Returns session info dict.

    Background (task description, deps, PRs, notes) is injected via the agent's
    `--append-system-prompt` flag at launch time -- so it lives in Claude's
    system prompt and survives /clear + resume without any flag tracking.
    The returned `prompt` is just the action instruction (if any); the UI
    types that into the TUI as user input to trigger work.
    """
    proj = app_state._db.get_project(project_id)
    if not proj:
        raise ValueError(f"Project not found: {project_id}")

    action = app_state._db.get_action(action_id)
    if not action:
        raise ValueError(f"Action not found: {action_id}")

    task = app_state._db.get_task(project_id, task_id)
    if not task:
        raise ValueError(f"Task not found: {task_id}")

    project_name = proj.get("name", project_id)
    design_doc = proj.get("design_doc")

    dep_statuses = {}
    for dep_id in task.get("dependencies", []):
        dep_task = app_state._db.get_task(project_id, dep_id)
        dep_statuses[dep_id] = dep_task["status"] if dep_task else "unknown"

    pr_context = {"number": pr_number, "repo": pr_repo} if pr_number and pr_repo else None

    action_prompt = custom_prompt if custom_prompt else action["prompt_template"]
    bg_system = build_background_system(task, project_name, dep_statuses,
                                        pr_context=pr_context, design_doc=design_doc)

    session_name = task_id
    session = app_state._db.get_session(task_id)
    is_new = session is None
    needs_launch = is_new or not session_exists(session_name)

    if needs_launch:
        working_dir = proj.get("working_dir", "~")
        from . import agent as _agent
        argv = _agent.launch_argv(session_name, system_prompt=bg_system)
        launch_session_argv(session_name, working_dir, argv)

    if is_new:
        app_state._db.create_session(task_id, project_id)
        # Seed the session-state cache so the new row appears in the
        # global snapshot before the agent's SessionStart hook (2-5s lag)
        # gets a chance to fire. Emits `session.state` on the bus,
        # which the frontend SessionStatusProvider consumes.
        from . import session_state
        session_state.set_state(
            session_name, state="starting",
            kind="task",
            project_id=project_id, target_id=task_id,
        )
        # Legacy `session.opened` ping kept for any non-state listeners
        # that just want to react to the open event.
        app_state.emit_event("session.opened", {
            "title": f"Session opened: {session_name}",
            "message": project_id,
            "severity": "info",
            "session": session_name,
        }, persist=False)

    return {
        "session": session_name,
        "new": is_new,
        "prompt": action_prompt,
    }


def fire_action(
    project_id: str,
    task_id: str,
    action_id: str,
    *,
    custom_prompt: str | None = None,
    pr_number: int | None = None,
    pr_repo: str | None = None,
    reason: str = "",
) -> dict | None:
    """Server-side equivalent of clicking an action button in the UI.

    Opens (or resumes) the task's session via `open_session`, then
    delivers the action prompt into its TUI on a background thread
    so the caller is never blocked on agent startup. Used by event
    triggers (e.g. PR merged -> auto-fire `sync`) where the
    delivery has to happen from the server process, no frontend
    involved.

    Returns the `open_session()` result on success, or None when
    `open_session` raised (e.g. unknown action id, missing task).
    Errors during delivery are logged but never propagate.
    """
    import threading

    try:
        result = open_session(
            task_id=task_id, project_id=project_id,
            action_id=action_id, custom_prompt=custom_prompt,
            pr_number=pr_number, pr_repo=pr_repo,
        )
    except (KeyError, ValueError) as e:
        print(f"[fire_action] open_session failed for "
              f"{project_id}/{task_id}/{action_id}: {e}", flush=True)
        return None

    session_name = result.get("session", "")
    prompt_text = result.get("prompt") or ""
    if not session_name or not prompt_text:
        return result

    def _deliver():
        try:
            from adapters.tmux import paste_text, wait_until_ready
            if wait_until_ready(session_name, timeout_secs=60):
                paste_text(session_name, prompt_text)
                tag = f" ({reason})" if reason else ""
                print(f"[fire_action] delivered '{action_id}' to "
                      f"{session_name}{tag}", flush=True)
            else:
                print(f"[fire_action] {session_name} not ready in 60s; "
                      f"prompt skipped", flush=True)
        except Exception as e:  # noqa: BLE001 -- log + drop, automation
            print(f"[fire_action] deliver failed for {session_name}: {e}",
                  flush=True)

    threading.Thread(
        target=_deliver, name=f"fire-action-{session_name}", daemon=True,
    ).start()
    return result


def resume_session(session_name):
    """Re-open the tmux wrapper and resume the existing agent conversation.

    Use case: the host rebooted -> every tmux server on disk is gone,
    but the agent's session files under ~/.claude/projects/ still
    hold the full conversation history. Instead of killing the Eva
    session row (which orphans that history behind a deleted task_id),
    this restarts tmux with the same name and runs the agent's resume
    subcommand inside so the agent picks up exactly where it left off.

    Fallback: if we never captured an `agent_session_id` (e.g. a row
    written before SessionStart fired), launch a fresh agent instead
    so the card at least comes back alive.
    """
    session = app_state._db.get_session(session_name)
    if not session:
        raise ValueError(f"Session not found: {session_name}")
    if session_exists(session_name):
        return {"session": session_name, "action": "noop", "running": True}

    project_id = session.get("project", "") or ""
    proj = app_state._db.get_project(project_id) if project_id else None
    working_dir = (proj.get("working_dir", "~") if proj else "~") or "~"

    from . import agent as _agent
    uuid = (session.get("agent_session_id") or "").strip()
    if uuid:
        # Use the agent's own `resume` subcommand (NOT `--resume`,
        # which transparent-passes through to claude). An exact UUID
        # match resumes non-interactively -- required because the
        # tmux session we spawn is detached and can't drive an
        # interactive picker.
        argv = _agent.resume_argv(uuid)
        action = "resumed"
    else:
        # No UUID on record (legacy row written before the column
        # existed). Best effort: launch a fresh agent under the same
        # tmux name so the card comes back alive; conversation history
        # is lost in this branch.
        argv = _agent.launch_argv(session_name)
        action = "relaunched"
    launch_session_argv(session_name, working_dir, argv)

    app_state.emit_event("session.opened", {
        "title": f"Session resumed: {session_name}",
        "message": project_id,
        "severity": "info",
        "session": project_id,
    }, persist=False)
    return {"session": session_name, "action": action, "running": True, "agent_session_id": uuid}


def kill_session(session_name):
    """Kill tmux session and clean up DB. Returns result dict.

    Also emits a `session.killed` event so the UI refreshes without
    needing a manual reload (the agent itself can't fire its Stop hook
    after tmux-kill, so we're the only source of truth for this event)."""
    session = app_state._db.get_session(session_name)
    project = session.get("project", "") if session else ""
    if session_exists(session_name):
        graceful_kill_session(session_name)
    app_state._db.delete_session(session_name)
    app_state.emit_event("session.killed", {
        "title": f"Session killed: {session_name}",
        "message": project,
        "severity": "info",
        "session": project,
    }, persist=False)
    return {"status": "killed", "session": session_name}


def get_session_status(session_name):
    """Get session status from the unified `session_state` cache.
    Falls back to "not_found" if neither the cache nor the DB row knows
    about this session.
    """
    from . import session_state
    session = app_state._db.get_session(session_name)
    running = session_exists(session_name)
    cache_row = session_state.get(session_name) or {}
    cache_state = cache_row.get("state", "")
    if cache_state:
        status = cache_state
    elif session is not None:
        status = "unknown"
    else:
        status = "not_found"
    return {
        "session": session_name,
        "running": running,
        "status": status,
        "exists_in_db": session is not None,
    }


_PROJECT_SESSION_PREFIX = "pm-"


def project_session_tmux_name(project_id: str) -> str:
    """Tmux session name for a project's manager session. Prefixed so it
    can't collide with a per-task session (tasks never start with `pm-`
    by convention)."""
    return _PROJECT_SESSION_PREFIX + project_id


def build_project_background_system(project: dict, tasks: dict) -> str:
    """System prompt injected into a project-manager agent session.

    Sets the manager role (audit + suggest, never code), summarises the
    project state at startup, and points at eva-cli for live introspection
    so the agent can refresh facts as needed instead of relying on the
    stale snapshot.
    """
    pid = project.get("id") or project.get("project_id") or ""
    name = project.get("name") or pid
    desc = project.get("description") or ""
    design = project.get("design_doc") or ""
    has_tickets = project.get("has_tickets")

    counts: dict[str, int] = {}
    for t in tasks.values():
        s = (t.get("status") or "not_started")
        counts[s] = counts.get(s, 0) + 1

    sample_tids = list(tasks.keys())[:5]

    lines = [
        f"[Role] You are the project manager for `{name}` (id: {pid}).",
        "Your job is to coordinate, audit, and recommend -- NOT to write",
        "code or open PRs. Each task has its own worker session for that.",
        "Always work in REPORT mode unless the user explicitly asks you to",
        "fix something. When you spot anomalies (status mismatches, blocked",
        "tasks, stale PRs, missing tickets) describe what's wrong and what",
        "the user could do, then stop.",
        "",
        f"[Project] {name} | {len(tasks)} tasks | tickets={'on' if has_tickets else 'off'}",
    ]
    if desc:
        lines.append(f"Description: {desc}")
    if design:
        lines.append(f"Design doc: {design}")
    if counts:
        bits = [f"{s}={n}" for s, n in sorted(counts.items())]
        lines.append("Status counts: " + ", ".join(bits))
    if sample_tids:
        lines.append("Sample task ids: " + ", ".join(sample_tids))

    lines.extend([
        "",
        "[Tools] Use `eva-cli` for everything -- introspection AND",
        "interacting with task workers. Never shell out to tmux yourself,",
        "never call backend HTTP endpoints, never edit files. The CLI is",
        "the single boundary between you and Eva's state.",
        "",
        "Common reads:",
        f"  eva-cli list-tasks {pid}                      # current task list",
        f"  eva-cli get-task {pid} <task_id>              # one task in detail",
        f"  eva-cli list-history {pid} <task_id>          # append-only task timeline",
        f"  eva-cli list-prs --project {pid}              # PRs scoped to this project",
        f"  eva-cli check-status {pid} <task_id>          # sync from JIRA/GitHub",
        "  eva-cli list-sessions                         # all active worker sessions",
        "  eva-cli help                                  # everything else",
        "Most commands take project as a POSITIONAL first arg; `list-prs` and",
        "`list-sessions` are global (list-prs accepts optional --project / --status).",
        "Use --json on any command for machine-readable output.",
        "",
        "Spawn a task worker (with the user's go-ahead):",
        f"  eva-cli open-session {pid} <task_id> --action <id>",
        "    # action: open / do-task / evaluate / sync / create-ticket",
        f"  eva-cli open-session {pid} <task_id> --action fix-ci \\",
        "      --pr-number <N> --pr-repo <org/repo>",
        "    # pr-context: fix-ci / address-comments / draft-reply / auto-pr-tend",
        "",
        "Talk to a running task worker (NOT a chat -- this pastes a single",
        "instruction into the worker's TUI and submits it):",
        "  eva-cli send-message <task_id> '<one-line instruction>'",
        "    # session_name == task_id for task workers; review sessions",
        "    # use the review-* names from list-sessions.",
        "  eva-cli send-message <task_id> 'rebase on master then re-run CI' \\",
        "      --no-wait    # skip TUI ready probe if you know it's idle",
        "Prefer ONE-line instructions. Long chats belong on the user's",
        "screen, not buried in a paste.",
        "",
        "[Boundaries] Do NOT: open PRs, push branches, edit files, kill",
        "task sessions, or create/close tasks unless the user explicitly",
        "tells you to. Suggesting + asking confirmation is always safer.",
        "Spawning a worker (`open-session`) and pasting an instruction",
        "(`send-message`) ARE allowed once the user agrees -- coordination",
        "is your job; doing the actual work is the worker's.",
        "",
        "[Language] Reply in Chinese (中文).",
    ])
    return "\n".join(lines)


def open_project_session(project_id: str) -> dict:
    """Open (or resume) the project-manager session for `project_id`.

    Idempotent: if the tmux session already exists we just return the
    record. Otherwise we launch the agent with the project background baked
    into --append-system-prompt and persist a row so the frontend can
    track status across restarts.
    """
    proj = app_state._db.get_project(project_id)
    if not proj:
        raise ValueError(f"Project not found: {project_id}")

    tmux_name = project_session_tmux_name(project_id)
    record = app_state._db.get_project_session(project_id)
    is_new = record is None

    if is_new or not session_exists(tmux_name):
        tasks = app_state.load_tasks(project_id)
        bg = build_project_background_system(proj, tasks)
        working_dir = proj.get("working_dir", "~")
        from . import agent as _agent
        argv = _agent.launch_argv(tmux_name, system_prompt=bg)
        launch_session_argv(tmux_name, working_dir, argv)
        app_state._db.create_project_session(project_id, tmux_name)
        if is_new:
            app_state.emit_event("session.opened", {
                "title": f"Project session opened: {project_id}",
                "message": project_id,
                "severity": "info",
                "session": tmux_name,
            }, persist=False)

    return {
        "project_id": project_id,
        "tmux_name": tmux_name,
        "running": session_exists(tmux_name),
        **(app_state._db.get_project_session(project_id) or {}),
    }


def get_project_session(project_id: str) -> dict | None:
    """Return current state of the project-manager session, or None if
    there's no record. Always re-checks tmux liveness."""
    record = app_state._db.get_project_session(project_id)
    if not record:
        return None
    return {**record, "running": session_exists(record["tmux_name"])}


def kill_project_session(project_id: str) -> dict:
    """Kill the project-manager session and forget the row."""
    record = app_state._db.get_project_session(project_id)
    if not record:
        return {"killed": False, "reason": "not_found"}
    tmux_name = record["tmux_name"]
    if session_exists(tmux_name):
        graceful_kill_session(tmux_name)
    app_state._db.delete_project_session(project_id)
    app_state.emit_event("session.killed", {
        "title": f"Project session killed: {project_id}",
        "message": project_id,
        "severity": "info",
        "session": tmux_name,
    }, persist=False)
    return {"killed": True, "tmux_name": tmux_name}


def build_background_system(
    task_data: dict,
    project_name: str,
    dep_statuses: dict,
    pr_context: dict = None,
    design_doc: str = None,
) -> str:
    """Build the [Background] block injected as claude's system prompt.

    Structured, one line per field so the model can skim. Deliberately
    omits the [Action] -- that's delivered as a user turn after startup
    so it shows in the TUI and reads like a real instruction.
    """
    tid = task_data.get("ticket_id")
    turl = task_data.get("ticket_url", "")
    ticket_str = f" | {tid}" + (f" {turl}" if turl else "") if tid else ""
    status = task_data.get("status", "not_started")

    lines = [f"[Background] {project_name} | {task_data['task_id']} ({status}){ticket_str}"]
    lines.append(task_data.get("description", ""))

    if design_doc:
        lines.append(f"Design: {design_doc} -- read and respect before starting.")

    deps = task_data.get("dependencies", [])
    if deps:
        lines.append("Deps: " + ", ".join(f"{d}({dep_statuses.get(d, '?')})" for d in deps))

    prs = task_data.get("prs", [])
    if prs:
        for pr in prs:
            url = pr.get("url") or f"https://github.com/unknown/pull/{pr['number']}"
            lines.append(f"PR: {url} [{pr.get('status','open')}] {pr.get('title','')}")

    if pr_context:
        pr_num = pr_context.get("number")
        matched = next((p for p in prs if p["number"] == pr_num), None)
        if matched:
            lines.append(f"Focus PR #{pr_num}: {pr_context.get('repo','')} branch={matched.get('head_branch','')} ci={matched.get('ci_status','?')} review={matched.get('review_status','')}")

    lines.append("")
    lines.append("[Tools] eva-cli -- run `eva-cli --help` for task/PR/session management.")
    lines.append("[History] After each meaningful step (commit, PR event, blocker) run:")
    lines.append(f"  eva-cli append-history {task_data.get('project','<proj>')} {task_data.get('task_id','<task>')} \"<=100 chars, terse\"")
    lines.append("Keep each line one fact: what you did or what's blocking. Append-only timeline, no editing old lines.")
    lines.append("Also call `eva-cli check-status` when you think status should change.")
    lines.append("[Language] Reply in Chinese (中文).")

    return "\n".join(lines)


def build_background(
    task_data: dict,
    project_name: str,
    prompt_template: str,
    dep_statuses: dict,
    pr_context: dict = None,
    design_doc: str = None,
) -> str:
    """Legacy: [Background] + [Action] in a single block. Kept for the
    few call sites that still need the combined form. New code should
    use `build_background_system` + deliver the action separately."""
    bg = build_background_system(task_data, project_name, dep_statuses,
                                 pr_context=pr_context, design_doc=design_doc)
    if prompt_template:
        return bg + "\n\n[Action]\n" + prompt_template
    return bg
