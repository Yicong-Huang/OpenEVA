"""Review lifecycle: PR -> session -> history.

A `Review` is the reviewer-side record for a PR someone else owns. Unlike
`Task`, which has status + ticket + deps + subordinate PRs, a review is
1:1 with a row in `review_prs` and its state lives inline on that row
(session_name, my_workflow_state, started_at, ...). The append-only log
of what I did lives in `review_history`.

This module is the analog of `core/sessions.py` for reviews. It owns:
  * session naming (stable, URL-derived slug)
  * action-button prompt assembly (pulling live PR metadata into the
    prompt so the agent has full context at launch)
  * tmux launch + update of review_prs workflow columns
"""

import re as _re

import app_state
from adapters.tmux import launch_session_argv, session_exists


# Keep in sync with action_definitions rows whose context='review'. Only
# these action ids are valid on /api/reviews/{url}/open so a typo'd id
# can't sneak a task-context prompt into a review session.
REVIEW_ACTIONS = frozenset({"review-pr", "review-reply", "review-sync"})


def _slugify(text: str) -> str:
    """Lowercase, strip non-alphanumeric. Used for repo owner/name pieces
    so session names stay tmux-safe (tmux dislikes '/', '.', etc.)."""
    cleaned = _re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    return cleaned or "x"


def session_name_for(repo: str, number: int) -> str:
    """Derive a stable tmux session name for a review PR.

    Shape: `review-<owner>-<repo>-<number>`. Uses both owner and repo
    so e.g. `acme/widgets` and a hypothetical `myorg/widgets` don't
    collide. Pure function -- safe to call from the UI too if we need
    to pre-compute the name before the backend creates the row.
    """
    owner, _, name = (repo or "").partition("/")
    return f"review-{_slugify(owner)}-{_slugify(name)}-{int(number)}"


def build_review_system_prompt(pr_row: dict) -> str:
    """Build the [Background] block that gets injected as Claude's
    system prompt at review-session launch time. Mirrors task
    sessions' `sessions.build_background_system` -- the PR
    metadata persists across /clear and resume because it lives in
    the system prompt, not in a one-off user message.

    Post-Phase-2 merge: a "review" is just a task with `type='review'`
    whose task_id matches the tmux session name (`review-<owner>-<repo>-<n>`).
    The same eva-cli verbs (append-history, add-pr, check-status) that
    feature/bug tasks use apply here -- mention them so the agent
    doesn't treat reviews as a parallel universe.
    """
    repo = pr_row.get("repo") or ""
    number = pr_row.get("number")
    url = pr_row.get("url") or ""
    title = pr_row.get("title") or ""
    author = pr_row.get("author") or ""
    head_branch = pr_row.get("head_branch") or ""
    base_branch = pr_row.get("base_branch") or ""
    additions = pr_row.get("additions") or 0
    deletions = pr_row.get("deletions") or 0
    pr_status = pr_row.get("status") or ""
    ci_status = pr_row.get("ci_status") or ""
    review_decision = pr_row.get("review_status") or ""
    my_stance = pr_row.get("my_review_state") or ""

    # Derive the review task_id (matches the tmux session name + the
    # `tasks.task_id` post-merge). Falls back to a placeholder if URL
    # is malformed -- the prompt is still useful even without the
    # exact id.
    import re as _re
    m = _re.search(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)", url)
    if m:
        slug = _re.sub(r"[^a-zA-Z0-9]+", "-",
                       f"{m.group(1)}/{m.group(2)}").strip("-").lower()
        task_id = f"review-{slug}-{m.group(3)}"
    else:
        task_id = "<review-task-id>"

    lines = [
        f"[Review] {repo}#{number}: {title}".rstrip(),
        f"[URL] {url}",
        (
            f"[Meta] author={author} | branch={head_branch} -> "
            f"{base_branch} | diff=+{additions}/-{deletions}"
        ),
        (
            f"[Status] pr={pr_status or '?'} | ci={ci_status or '?'} | "
            f"review_decision={review_decision or '?'} | "
            f"my_stance={my_stance or '(none)'}"
        ),
        "",
        "[Tools] eva-cli -- run `eva-cli --help` for task/PR/session management.",
        "This review is just a task (type='review', task_id matches the "
        "tmux session name above). The same CRUD applies as for feature/"
        "bug tasks -- append history, link related PRs, change status.",
        "[History] After each meaningful pass run:",
        f"  eva-cli append-history \"\" {task_id} \"<=100 chars, terse>\"",
        "Append-only timeline. One fact per line: what you did or what's blocked.",
        "[Link PR] Review-type tasks conventionally stay 1:1 with the PR "
        "being reviewed (already attached). If you need to reference an "
        "additional related PR for context, attach it explicitly:",
        f"  eva-cli add-pr \"\" {task_id} <pr_number> <pr_url>",
        "[Language] Reply in Chinese (中文).",
    ]
    return "\n".join(lines)


def _resolve_review_target(
    review_url: str, action_id: str,
) -> tuple[dict, dict]:
    """Validate `(review_url, action_id)` and return `(pr_row, action)`.
    Raises ValueError when the review doesn't exist, the action id
    isn't a review-context action, or the action_definitions row is
    missing (DB drift)."""
    pr_row = app_state._db.get_review_pr(review_url)
    if not pr_row:
        raise ValueError(f"Review PR not found: {review_url}")
    if action_id not in REVIEW_ACTIONS:
        raise ValueError(
            f"action {action_id!r} is not a review action; "
            f"valid: {sorted(REVIEW_ACTIONS)}"
        )
    action = app_state._db.get_action(action_id)
    if not action:
        raise ValueError(f"Action not found: {action_id}")
    return pr_row, action


def _persist_review_session_state(
    pr_row: dict, session_name: str,
) -> None:
    """Stamp the session_name + workflow state + started_at fields
    onto the review_prs row. Called even when the tmux session
    already exists so the row reflects the most recent open click."""
    from eva_db import _now_iso
    updates = {
        "session_name": session_name,
        "my_workflow_state": "active",
    }
    if not pr_row.get("started_at"):
        updates["started_at"] = _now_iso()
    app_state._db.upsert_review_pr(
        url=pr_row["url"], repo=pr_row["repo"],
        number=pr_row["number"], **updates,
    )


def _launch_new_review_session(
    session_name: str, review_url: str, action_id: str,
    pr_row: dict,
) -> None:
    """Launch tmux + the agent for a fresh review session, injecting the
    PR metadata as Claude's system prompt (`--append-system-prompt`)
    so the background survives /clear and resume -- same shape as
    task sessions, just with review context.

    Reviews don't belong to a project, so cwd is the user's home dir.
    """
    bg_system = build_review_system_prompt(pr_row)
    from . import agent as _agent
    launch_session_argv(
        session_name, "~",
        _agent.launch_argv(session_name, system_prompt=bg_system),
    )
    try:
        app_state._db.append_review_history(
            review_url, f"session started: {action_id}", source="system",
        )
    except ValueError:
        # Audit-log failures must not break the launch -- the session
        # is already up and that's the important bit.
        pass


def open_review_session(review_url: str, action_id: str = "review-pr",
                        custom_prompt: str = None) -> dict:
    """Launch or resume the review session for a PR. Returns dict with
    `session`, `prompt`, and `new` fields (mirrors sessions.open_session).

    Side-effects:
      * writes `session_name`, `my_workflow_state='active'`, `started_at`
        back to `review_prs` so subsequent loads know the state
      * emits a `review.session.opened` event so the frontend refreshes
      * appends a review_history entry tagged source='system' on first
        launch (so users can see "I opened this at X")
    """
    pr_row, action = _resolve_review_target(review_url, action_id)
    session_name = pr_row.get("session_name") or session_name_for(
        pr_row["repo"], pr_row["number"])
    is_new = not session_exists(session_name)

    _persist_review_session_state(pr_row, session_name)

    # The action template is the user message (action instruction
    # only); PR metadata lives in the system prompt now (mirrors
    # task sessions). custom_prompt overrides the template for one-off
    # flows like Ask Agent.
    prompt = (custom_prompt if custom_prompt is not None
              else action.get("prompt_template", ""))

    if is_new:
        _launch_new_review_session(session_name, review_url, action_id, pr_row)

    # Emit on re-click too: the frontend needs a refresh signal
    # regardless of whether we spun up tmux. Without this the second
    # click on "Review PR" (session already live) would POST but never
    # notify ReviewCard to reload state/history.
    app_state.emit_event("review.session.opened", {
        "title": f"Review session opened: {session_name}",
        "message": review_url,
        "severity": "info",
        "session": session_name,
    }, persist=False)

    return {
        "session": session_name,
        "new": is_new,
        "prompt": prompt,
        "review_url": review_url,
    }


def mark_review_seen(review_url: str) -> dict:
    """Snapshot the review's current comment_count into
    last_seen_comment_count so the "N new" badge clears. Called when
    the user opens a review (PR Card / ReviewsPage selectedPR
    transition). Idempotent.

    Raises ValueError when the URL isn't in the queue (route maps
    that to 422 / 404).
    """
    pr_row = app_state._db.get_review_pr(review_url)
    if not pr_row:
        raise ValueError(f"Review PR not found: {review_url}")
    app_state._db.mark_review_seen(review_url)
    return app_state._db.get_review_pr(review_url)


def update_review(review_url: str, **fields) -> dict:
    """Whitelisted update for reviewer-controlled fields on review_prs.
    Only `my_workflow_state` is editable today (notes went away when we
    chose review_history over a notes column). Returns the updated row."""
    pr_row = app_state._db.get_review_pr(review_url)
    if not pr_row:
        raise ValueError(f"Review PR not found: {review_url}")

    allowed = {"my_workflow_state"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return pr_row

    app_state._db.upsert_review_pr(
        url=pr_row["url"], repo=pr_row["repo"], number=pr_row["number"],
        **updates,
    )
    return app_state._db.get_review_pr(review_url)
