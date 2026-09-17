"""Session management core logic. Direct tmux + config_db access."""

import glob
import json
import os
import time

import app_state
from adapters.tmux import session_exists, launch_session_argv, graceful_kill_session

# Root where Claude Code stores per-cwd transcripts
# (`<root>/<encoded-cwd>/<uuid>.jsonl`). Module-level so tests can
# repoint it at a temp dir. `os.path.expanduser` is deferred to call
# time so a test monkeypatching this constant takes effect.
CLAUDE_PROJECTS_DIR = "~/.claude/projects"


def _local_transcript_cwd(session_uuid: str) -> str | None:
    """Return the original working directory of a local Claude session.

    `claude --resume UUID` is cwd-sensitive: it only finds the
    transcript when launched from the same directory the session was
    created in (Claude keys transcripts by an encoding of that cwd).
    We recover that directory from the transcript itself -- the first
    record carries a `cwd` field -- so resume can launch from the right
    place instead of a hardcoded `~`.

    Returns None when no transcript exists for the uuid (e.g. a cloud
    session id, or a transcript that was deleted), letting the caller
    fall back to its default working dir."""
    if not session_uuid:
        return None
    root = os.path.expanduser(CLAUDE_PROJECTS_DIR)
    matches = glob.glob(os.path.join(root, "*", f"{session_uuid}.jsonl"))
    if not matches:
        return None
    try:
        with open(matches[0], encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                cwd = rec.get("cwd")
                if cwd:
                    return cwd
    except OSError:
        return None
    return None


def _local_transcript_exists(session_uuid: str) -> bool:
    """True iff a local transcript file exists for `session_uuid`.

    `claude/isaac --resume UUID` exits 1 immediately ("No conversation
    found with session ID: ...") when the transcript is gone -- and since
    that process IS the tmux pane's command, the pane dies with it a
    moment after `new-session` reported success. Resuming a dead UUID
    therefore looks like "the session won't connect" in the UI.

    Callers check this BEFORE choosing `--resume` so a vanished
    transcript degrades to a fresh launch instead of a guaranteed crash.

    Only meaningful for local transcript UUIDs. Cloud session ids have
    no local file, so callers must not gate cloud resume on this."""
    if not session_uuid:
        return False
    root = os.path.expanduser(CLAUDE_PROJECTS_DIR)
    return bool(glob.glob(os.path.join(root, "*", f"{session_uuid}.jsonl")))


# How long to watch a just-launched pane for an immediate self-exit, and
# how often to re-probe. A resume that fails (vanished transcript, bad
# cwd, missing binary) dies in well under a second, so a short window
# catches it. This cost is paid on every resume -- including the startup
# recovery pass, which resumes sessions serially -- so keep it small.
_PANE_CONFIRM_TIMEOUT = 1.0
_PANE_CONFIRM_INTERVAL = 0.2


def _confirm_pane_alive(session_name: str,
                        timeout: float | None = None) -> bool:
    """True iff the tmux pane survives a short observation window.

    `tmux new-session` exits 0 as soon as it has forked the pane, which
    says nothing about whether the command inside stayed up. When the
    agent exits at startup (vanished transcript, bad cwd, missing binary)
    tmux reaps the pane a beat later -- so a naive "we launched it"
    success claim leaves the UI showing a session nothing is behind.

    Returns False as soon as the pane is observed gone; otherwise watches
    until `timeout` elapses and reports it alive. The full window is only
    spent on the healthy path, so keep `timeout` small (see the constant).

    `timeout=None` reads `_PANE_CONFIRM_TIMEOUT` at CALL time (not as a
    default-arg binding) so tests can shrink the window by patching the
    module constant."""
    if timeout is None:
        timeout = _PANE_CONFIRM_TIMEOUT
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        if not session_exists(session_name):
            return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(_PANE_CONFIRM_INTERVAL)


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
    """List all sessions grouped by project. Returns dict of
    {project_id: {name, sessions}}. Hidden projects (per
    `ui.hidden_projects`) are skipped so the Live-Tasks view stays
    consistent with the sidebar."""
    from . import settings as _settings
    hidden = _settings.get_hidden_projects()
    all_sessions = app_state._db.list_sessions()
    result = {}
    for s in all_sessions:
        tmux_name = s.get("tmux_name") or s.get("task_id") or ""
        if tmux_name.startswith(("ticket-", "review-", "cron-")):
            continue
        pid = s["project"]
        if pid in hidden:
            continue
        if pid not in result:
            proj = app_state._db.get_project(pid)
            proj_name = proj.get("name", pid) if proj else pid
            result[pid] = {"name": proj_name, "sessions": []}
        result[pid]["sessions"].append(_enrich(s))
    return result


def open_session(task_id, project_id, action_id="open", custom_prompt=None,
                 pr_number=None, pr_repo=None, agent_id=None):
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

    from . import agent as _agent
    # `agent_id` is the explicit pick the UI sends when the user enabled
    # several agents and chose one at open time; without it we resolve
    # the sole enabled agent or the default fallback. Bad id -> ValueError
    # (surfaced as 422 by the route).
    new_agent = _agent.resolve_new_session_agent(agent_id)
    # Agents without a launch-time system-prompt channel (e.g. Codex)
    # can't carry `bg_system` in the launch argv. For them we launch
    # bare and fold the background into the FIRST delivered prompt
    # instead (see the return value below). `needs_launch` marks that
    # first turn -- a re-click on an already-running session skips this.
    fold_bg_into_prompt = needs_launch and not new_agent.system_prompt_via_launch
    if needs_launch:
        working_dir = proj.get("working_dir", "~")
        launch_system = bg_system if new_agent.system_prompt_via_launch else None
        argv = new_agent.launch_argv(session_name, system_prompt=launch_system)
        launch_session_argv(session_name, working_dir, argv)

    if is_new:
        # Record which agent launched the session so resume uses the
        # same one (codex ids and Claude UUIDs aren't interchangeable).
        app_state._db.create_session(task_id, project_id,
                                     agent_impl=new_agent.id)
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
    elif needs_launch:
        # Row exists but the pane was gone, so we just launched a fresh
        # process (not a resume) with `new_agent` -- keep agent_impl in
        # sync with what's actually running.
        app_state._db.update_session(task_id, agent_impl=new_agent.id)

    # For agents that can't take a launch-time system prompt, prepend the
    # background to the first delivered prompt so the session still gets
    # its context (Codex reads the whole first message as turn one).
    delivered_prompt = action_prompt
    if fold_bg_into_prompt and bg_system:
        delivered_prompt = (f"{bg_system}\n\n{action_prompt}"
                            if action_prompt else bg_system)

    return {
        "session": session_name,
        "new": is_new,
        "prompt": delivered_prompt,
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
    # Resume with the agent that launched this session, not the global
    # active agent -- codex session ids and Claude transcript UUIDs are
    # not interchangeable. Empty `agent_impl` (legacy row) resolves to
    # the default agent inside `get_agent_by_id`.
    sess_agent = _agent.get_agent_by_id(session.get("agent_impl", "") or "")
    uuid = (session.get("agent_session_id") or "").strip()
    # A local transcript UUID whose .jsonl is gone can NOT be resumed:
    # the agent exits 1 ("No conversation found with session ID") and,
    # because that process is the pane's command, tmux tears the pane
    # down a moment after `new-session` returned success -- the UI then
    # shows a session that simply won't connect. Detect the vanished
    # transcript up front and degrade to a fresh launch instead.
    # Cloud ids (non-UUID shape) have no local file, so they skip this.
    resumable = bool(uuid)
    if uuid and _agent._looks_like_session_uuid(uuid) \
            and not _local_transcript_exists(uuid):
        print(f"[resume_session] {session_name}: transcript for {uuid} is "
              f"gone; launching a fresh agent instead", flush=True)
        resumable = False
    if resumable:
        # `resume_argv` routes by id shape: a transcript UUID resumes
        # the LOCAL conversation via `--resume` (cloud-independent),
        # while a numeric cloud id goes through the cloud `resume`
        # subcommand. Local resume is cwd-sensitive, so launch from the
        # directory the session was actually created in (recovered from
        # the transcript) rather than the project/`~` default. Cloud
        # ids have no local transcript and keep the project working dir.
        transcript_cwd = _local_transcript_cwd(uuid)
        if transcript_cwd:
            working_dir = transcript_cwd
        argv = sess_agent.resume_argv(uuid)
        action = "resumed"
    else:
        # Either no UUID on record (legacy row written before the column
        # existed) or the UUID's transcript has vanished. Best effort:
        # launch a fresh agent under the same tmux name so the card comes
        # back alive; conversation history is lost in this branch.
        argv = sess_agent.launch_argv(session_name)
        action = "relaunched"
        if uuid:
            # Forget the dead UUID so the next startup recovery pass (and
            # the next manual resume) doesn't retry a resume that can only
            # ever fail. The fresh agent's SessionStart hook writes a new
            # one in its place.
            try:
                app_state._db.update_session(session_name, agent_session_id="")
            except Exception as e:  # noqa: BLE001 -- best effort cleanup
                print(f"[resume_session] could not clear dead uuid for "
                      f"{session_name}: {e}", flush=True)
            uuid = ""
    launch_session_argv(session_name, working_dir, argv)

    # `tmux new-session` returning 0 only means tmux forked the pane --
    # NOT that the agent inside survived. A resume against a dead
    # transcript, a missing binary, or a bad cwd exits within a beat and
    # tmux reaps the pane, so reporting success here would tell the UI a
    # session is live when nothing is listening. Confirm the pane is
    # still there after a short grace window and report what's real.
    running = _confirm_pane_alive(session_name)
    if not running:
        print(f"[resume_session] {session_name}: pane died immediately after "
              f"{action}; reporting failure", flush=True)

    # Flip the snapshot to `starting` immediately so the SessionCard
    # stops showing `stopped` (the state it was in while crashed).
    from . import session_state
    inferred_kind = session_state._infer_kind(session_name)
    task_id = session.get("task_id", "") or ""
    session_state.set_state(
        session_name,
        state="starting" if running else "crashed",
        detail=(f"resume ({action})" if running
                else f"{action} failed: agent exited at startup"),
        kind=inferred_kind,
        project_id=project_id,
        target_id=task_id,
        agent_session_id=uuid,
    )
    if not running:
        # Nothing to refine and nothing to announce -- the pane is gone, so
        # skip the pane-recheck timer and the `session.opened` event. The
        # caller gets `running: False` and surfaces the failure (the route
        # passes this through; SessionCard turns it into an error alert).
        return {"session": session_name, "action": action,
                "running": False, "agent_session_id": uuid}

    # `claude resume <uuid>` does NOT fire the SessionStart hook
    # (only fresh `claude` invocations do), so without this nudge the
    # snapshot would stay at 'starting' until the user typed a prompt
    # (UserPromptSubmit -> thinking) or the agent went idle (no hook
    # fires when nothing is happening). Schedule a one-shot tmux-pane
    # re-read so we can flip to whatever the TUI actually shows --
    # idle / thinking / needs_permission. Five seconds is enough for
    # claude's startup + TUI paint on this host; if the pane is still
    # blank `_state_from_tmux_pane` falls back to idle anyway.
    import threading
    def _refine_state_from_pane():
        try:
            state, detail = session_state._state_from_tmux_pane(session_name)
            session_state.set_state(
                session_name, state=state, detail=detail or f"resumed ({action})",
                kind=inferred_kind, project_id=project_id,
                target_id=task_id, agent_session_id=uuid,
            )
        except Exception as exc:
            print(f"[resume] pane recheck failed for {session_name}: {exc}",
                  flush=True)
    threading.Timer(5.0, _refine_state_from_pane).start()

    app_state.emit_event("session.opened", {
        "title": f"Session resumed: {session_name}",
        "message": project_id,
        "severity": "info",
        "session": project_id,
    }, persist=False)
    return {"session": session_name, "action": action, "running": True, "agent_session_id": uuid}


def restart_session(session_name):
    """Restart a live session: kill its tmux but KEEP the DB row (and
    thus the `agent_session_id`), then resume so the agent reconnects
    to the same conversation.

    Use case: the agent binary or its config was updated and the
    running session needs to be relaunched to pick up the change --
    without losing the conversation. This is the live-session analog
    of `resume_session` (which only relaunches an already-dead tmux).

    Distinct from `kill_session`, which deletes the DB row and forgets
    the agent session id. Here we graceful-kill tmux, wait for it to
    actually disappear, then hand off to `resume_session` (which only
    launches when tmux is absent and reuses the stored UUID)."""
    session = app_state._db.get_session(session_name)
    if not session:
        raise ValueError(f"Session not found: {session_name}")

    if session_exists(session_name):
        # Graceful kill (Ctrl+C + grace + tmux kill). Crucially we do
        # NOT call kill_session() here -- that would delete the DB row
        # and drop the UUID we need for resume.
        graceful_kill_session(session_name)
        # graceful_kill_session is synchronous through tmux kill-session,
        # but poll to be certain tmux is gone before resume re-binds the
        # name (resume no-ops if it still sees the session alive).
        for _ in range(20):
            if not session_exists(session_name):
                break
            time.sleep(0.25)

    result = resume_session(session_name)
    result["restarted"] = True
    return result


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

    # Existing row whose tmux died but whose transcript UUID is on record:
    # reconnect the conversation instead of starting a fresh one.
    if (not is_new and not session_exists(tmux_name)
            and (record.get("agent_session_id") or "").strip()):
        resumed = resume_project_session(project_id)
        if resumed.get("running"):
            return {
                "project_id": project_id,
                "tmux_name": tmux_name,
                "running": True,
                **(app_state._db.get_project_session(project_id) or {}),
            }
        # Resume failed (transcript gone / bad cwd) -- fall through to a
        # fresh launch below so the manager still comes back alive.

    if is_new or not session_exists(tmux_name):
        tasks = app_state.load_tasks(project_id)
        bg = build_project_background_system(proj, tasks)
        working_dir = proj.get("working_dir", "~")
        from . import agent as _agent
        new_agent = _agent.get_agent_for_new_session()
        argv = new_agent.launch_argv(tmux_name, system_prompt=bg)
        launch_session_argv(tmux_name, working_dir, argv)
        # Record which agent launched it so resume uses the same one.
        app_state._db.create_project_session(
            project_id, tmux_name, agent_impl=new_agent.id)
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


def list_project_manager_sessions(*, live_only: bool = True) -> list[dict]:
    """Return project-manager session rows enriched for UI lists.

    Project managers live in their own `project_sessions` table rather
    than the task `sessions` table, so `/api/all-sessions` intentionally
    does not include them. This helper gives Live Tasks one compact
    source for manager rows while respecting hidden projects.
    """
    from . import settings as _settings
    hidden = _settings.get_hidden_projects()
    out: list[dict] = []
    for row in app_state._db.list_project_sessions():
        pid = row.get("project_id", "")
        if pid in hidden:
            continue
        tmux_name = row.get("tmux_name", "")
        running = session_exists(tmux_name)
        if live_only and not running:
            continue
        proj = app_state._db.get_project(pid) or {}
        out.append({
            **row,
            "project_name": proj.get("name", pid),
            "running": running,
        })
    return out


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


def resume_project_session(project_id: str) -> dict:
    """Re-open a PM session's tmux wrapper and resume its agent
    conversation. The PM analog of `resume_session`.

    Use case: the host rebooted (every tmux server is gone) but the
    agent's transcript under ~/.claude/projects/ is intact. We restart
    tmux with the same `pm-<project_id>` name and run the agent's resume
    so it picks up where it left off. Falls back to a fresh launch (with
    the project background rebuilt) when there's no usable UUID.
    """
    record = app_state._db.get_project_session(project_id)
    if not record:
        raise ValueError(f"Project session not found: {project_id}")
    tmux_name = record["tmux_name"]
    if session_exists(tmux_name):
        return {"project_id": project_id, "tmux_name": tmux_name,
                "action": "noop", "running": True}

    proj = app_state._db.get_project(project_id)
    if not proj:
        raise ValueError(f"Project not found: {project_id}")
    working_dir = (proj.get("working_dir", "~") or "~")

    from . import agent as _agent
    sess_agent = _agent.get_agent_by_id(record.get("agent_impl", "") or "")
    uuid = (record.get("agent_session_id") or "").strip()
    # A local transcript UUID whose .jsonl is gone can't be resumed (the
    # agent exits and tmux reaps the pane), so degrade to a fresh launch.
    resumable = bool(uuid)
    if uuid and _agent._looks_like_session_uuid(uuid) \
            and not _local_transcript_exists(uuid):
        print(f"[resume_project_session] {tmux_name}: transcript for {uuid} "
              f"is gone; launching a fresh agent instead", flush=True)
        resumable = False

    if resumable:
        transcript_cwd = _local_transcript_cwd(uuid)
        if transcript_cwd:
            working_dir = transcript_cwd
        argv = sess_agent.resume_argv(uuid)
        action = "resumed"
    else:
        # No usable UUID: relaunch fresh with the project background baked
        # in so the manager still boots with its role + project state.
        tasks = app_state.load_tasks(project_id)
        bg = build_project_background_system(proj, tasks)
        argv = sess_agent.launch_argv(tmux_name, system_prompt=bg)
        action = "relaunched"
        if uuid:
            try:
                app_state._db.update_project_session(
                    project_id, agent_session_id="")
            except Exception as e:  # noqa: BLE001 -- best effort cleanup
                print(f"[resume_project_session] could not clear dead uuid "
                      f"for {tmux_name}: {e}", flush=True)
            uuid = ""

    launch_session_argv(tmux_name, working_dir, argv)
    running = _confirm_pane_alive(tmux_name)
    if not running:
        print(f"[resume_project_session] {tmux_name}: pane died immediately "
              f"after {action}; reporting failure", flush=True)

    from . import session_state
    session_state.set_state(
        tmux_name,
        state="starting" if running else "crashed",
        detail=(f"resume ({action})" if running
                else f"{action} failed: agent exited at startup"),
        kind="project",
        project_id=project_id,
        agent_session_id=uuid,
    )
    return {"project_id": project_id, "tmux_name": tmux_name,
            "action": action, "running": running, "agent_session_id": uuid}


# How many most-recent history entries to inline into the launch system
# prompt. History is capped at 50 by EvaDB._get_history; keep the prompt
# slice smaller so a long-lived task doesn't blow up every session launch.
_HISTORY_IN_PROMPT = 20


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

    # History is stored newest-first; prompts read naturally in chronological
    # order, with the current state last.
    history = task_data.get("history") or []
    if history:
        recent = list(reversed(history[:_HISTORY_IN_PROMPT]))
        elided = len(history) - len(recent)
        lines.append("")
        header = "[Timeline] What happened so far (oldest first):"
        if elided > 0:
            header = f"[Timeline] What happened so far ({elided} older elided, oldest shown first):"
        lines.append(header)
        for h in recent:
            ts = (h.get("ts") or "")[5:16].replace("T", " ")  # MM-DD HH:MM
            text = h.get("text") or ""
            lines.append(f"  {ts}  {text}" if ts else f"  {text}")

    proj_arg = task_data.get("project") or ""
    tid = task_data.get("task_id") or "<task>"
    # task.type drives surface (feature / bug / test / chore / review /
    # flaky-test / slow-test / compliance / ticket-derived / ...) but
    # the worker workflow is the same: any task can attach PRs and
    # append history. The model otherwise assumes "ticket" / "review"
    # are second-class and skips linking PRs to them.
    lines.append("")
    lines.append("[Tools] eva-cli -- run `eva-cli --help` for task/PR/session management.")
    lines.append("Every work item is a `task` with an open `type` field. "
                 "type=feature/bug/review/flaky-test/etc. is metadata only -- "
                 "the same CRUD applies (link PRs, append history, change status).")
    lines.append("[History] Continue the timeline above. After each meaningful step (commit, PR event, blocker) run:")
    lines.append(f"  eva-cli append-history {proj_arg or '\"\"'} {tid} \"<=100 chars, terse\"")
    lines.append("Keep each line one fact: what you did or what's blocking. "
                 "Append-only timeline, no editing old lines.")
    lines.append("[Link PR] When you open or discover a related PR, attach it:")
    lines.append(f"  eva-cli add-pr {proj_arg or '\"\"'} {tid} <pr_number> <pr_url>")
    lines.append("Multiple PRs per task are supported (review-type tasks "
                 "conventionally stay 1:1 with their PR; other types can "
                 "carry many).")
    lines.append("Also call `eva-cli check-status` when you think status should change.")
    # Task descriptions are snapshots and may become stale as the repository
    # changes. Tell the agent to prefer current tool output.
    lines.append("[Ground truth] Live tool output (Bash/Read/Grep) is authoritative. "
                 "Any line numbers, commit SHAs, or file layout cited above were "
                 "captured when the task was created and may have drifted. If a tool "
                 "result conflicts with them, the description is stale: trust the tool, "
                 "re-derive from it, and do NOT conclude your tools are polluted or broken.")
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
