"""PR management routes: CRUD, sync, detail, comments."""

import asyncio
import json as _json
from pysqlite3 import dbapi2 as sqlite3
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

import app_state
from adapters.tmux import session_exists
from utils import repo_from_pr_url

# Repos that need fork CI checks are owned by core/common.prs.py as `_FORK_CI_REPOS`.
# Routes imports it (instead of recomputing) so there's a single source of
# truth -- drift between the two copies would silently break fork CI.
from common.prs import (
    list_all_prs as _list_all_prs,
    add_pr as _add_pr,
    remove_pr as _remove_pr,
    get_pr_detail as _get_pr_detail_core,
    get_pr_info as _get_pr_info,
    _pr_info_cache,  # noqa: F401 -- re-exported via server.py
    _fetch_fork_ci,
    _aggregate_ci_status,
    _is_externally_merged,  # noqa: F401 -- re-exported via server.py
    _resolve_pr_status,
    _FORK_CI_REPOS as _fork_ci_repos,
    _SYNC_VIEW_FIELDS,
    # Discover helpers live in prs so the CLI (sync generator) and
    # the HTTP stream share one implementation -- keeping sync + async
    # discover in lockstep on field extraction, fork resolution, etc.
    build_search_args as _build_search_args,
    build_pr_search_cmd as _build_pr_search_cmd,
    ingest_discovered_item as _ingest_discovered_item,
)


@app_state.app.get("/api/pr-info")
def get_pr_info(url: str):
    """Fetch PR review info from GitHub. Caches for 5 min."""
    return _get_pr_info(url)


@app_state.app.get("/api/all-sessions")
def list_all_project_sessions():
    """List all sessions across all projects, grouped by project.

    Each group embeds the tasks actually referenced by its sessions plus
    the direct dependencies of those tasks (so `isTaskBlocked` can judge
    them without a second request). The frontend no longer needs to
    follow up with `/api/projects/{pid}` per project -- see SessionsPage
    for the consumer.
    """
    result: dict = {}
    all_sessions = app_state._db.list_sessions()
    for s in all_sessions:
        pid = s["project"]
        if pid not in result:
            proj = app_state._db.get_project(pid) or {}
            result[pid] = {
                "id": pid,
                "name": proj.get("name", pid),
                "has_tickets": bool(proj.get("has_tickets", True)),
                "sessions": [],
                "tasks": {},
            }
        # Just the DB row + a tmux liveness flag. The frontend reads
        # the live state from the session-status snapshot service
        # (`/api/sessions/snapshot` + SSE patches), so we don't
        # double-stamp the status here any more.
        result[pid]["sessions"].append({
            **s,
            "running": session_exists(s["tmux_name"]),
        })

    # Pass 2: for each project, load the tasks referenced by its sessions
    # plus each of those tasks' direct dependencies. Also attach live
    # session info (name / running / status) the same way load_tasks does
    # for /api/projects/{pid} -- TaskCard renders its SessionCard from
    # `task.session`, so skipping this breaks the live terminal display.
    for pid, bundle in result.items():
        wanted: set[str] = set()
        for s in bundle["sessions"]:
            wanted.add(s["task_id"])
        # First fetch the session tasks to discover their dep ids.
        loaded: dict = {}
        for tid in list(wanted):
            task = app_state._db.get_task(pid, tid)
            if task:
                loaded[tid] = task
                for dep_id in task.get("dependencies", []) or []:
                    if dep_id not in loaded and dep_id not in wanted:
                        wanted.add(dep_id)
        # Now fetch any direct deps that weren't already the session's task.
        for tid in wanted - set(loaded.keys()):
            task = app_state._db.get_task(pid, tid)
            if task:
                loaded[tid] = task
        # Attach session metadata so TaskCard can render SessionCard. Keyed
        # by task_id since sessions 1:1 with tasks in Eva.
        session_map = {s["task_id"]: s for s in bundle["sessions"]}
        for tid, t in loaded.items():
            s = session_map.get(tid)
            if s:
                t["session"] = {
                    "name": s["tmux_name"],
                    "running": s.get("running", False),
                    # Status is the DB column; the TaskCard's
                    # SessionCard reads the live state from the
                    # snapshot service via `useSessionState`. This
                    # field is just the initial fallback.
                    "status": s.get("status", ""),
                }
        bundle["tasks"] = loaded

    return result


@app_state.app.get("/api/all-prs")
def list_all_prs(status: str = "", search: str = ""):
    """List all PRs from the tasks DB, grouped by project."""
    return _list_all_prs(status, search)


@app_state.app.get("/api/review-requests")
def list_review_requests():
    """Open PRs awaiting my review across both GitHub accounts.

    Driven by `gh search prs --review-requested=@me`, one call per
    account token. Unlike `/api/all-prs` this hits GitHub directly --
    no DB caching -- because the review queue changes faster than the
    PR sync cadence and we don't want stale entries here.
    """
    from common.prs import list_review_requests as _list_reviews
    return {"prs": _list_reviews()}


class ReviewWatchBody(BaseModel):
    url: str


@app_state.app.post("/api/review-requests/watchlist", status_code=201)
def add_review_watch(body: ReviewWatchBody):
    """Pin a PR URL to the manual review watchlist."""
    from common.prs import add_review_watch as _add_watch
    try:
        return _add_watch(body.url)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.delete("/api/review-requests/watchlist")
def remove_review_watch(url: str):
    """Remove a PR URL from the manual review watchlist."""
    from common.prs import remove_review_watch as _rm_watch
    if _rm_watch(url):
        return {"removed": True, "url": url}
    raise HTTPException(status_code=404, detail="URL not in watchlist")


class ReviewOpenBody(BaseModel):
    action_id: str = "review-pr"
    custom_prompt: Optional[str] = None


class ReviewUpdateBody(BaseModel):
    my_workflow_state: Optional[str] = None


class ReviewHistoryBody(BaseModel):
    text: str
    source: Optional[str] = "manual"


@app_state.app.post("/api/reviews/open")
def open_review_session_route(url: str, body: ReviewOpenBody):
    """Launch (or resume) the agent session for a review PR and return
    the prompt to send. `url` is a query string param because a PR URL
    embedded in a path segment is painful to escape cleanly on the client.

    Body shape mirrors /api/sessions/open (action_id, optional
    custom_prompt). 422 if the URL isn't in review_prs or the action
    isn't a review-context action.
    """
    from common.reviews import open_review_session
    try:
        return open_review_session(
            url, action_id=body.action_id,
            custom_prompt=body.custom_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.patch("/api/reviews")
def patch_review_route(url: str, body: ReviewUpdateBody):
    """Edit reviewer-controlled fields on a review_prs row. Currently
    only `my_workflow_state` is editable via the API."""
    from common.reviews import update_review
    try:
        return update_review(
            url, **{k: v for k, v in body.model_dump().items() if v is not None}
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.post("/api/reviews/history")
def append_review_history_route(url: str, body: ReviewHistoryBody):
    """Append a line to the review's history timeline. 422 on empty
    text or >100 chars (matches append_task_history's contract)."""
    try:
        return app_state._db.append_review_history(
            url, body.text, source=body.source or "manual",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.get("/api/reviews/history")
def list_review_history_route(url: str, limit: int = 50):
    """List review-history entries (newest first)."""
    return {
        "entries": app_state._db.list_review_history(url, limit=limit),
    }


@app_state.app.post("/api/reviews/seen")
def mark_review_seen_route(url: str):
    """Mark a review as seen -- snapshots comment_count into
    last_seen_comment_count so the "N new" badge resets. Called by
    the frontend when the user opens a review."""
    from common.reviews import mark_review_seen
    try:
        return mark_review_seen(url)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app_state.app.post("/api/prs/{number}/seen")
def mark_pr_seen_route(number: int):
    """All-PRs equivalent of /api/reviews/seen. Snapshots a task
    PR's `comment_count` into `last_seen_comment_count` so the
    "N new" badge clears. Called when the user selects a PR on
    the All-PRs / Project pages."""
    ok = app_state._db.mark_pr_seen(number)
    if not ok:
        raise HTTPException(status_code=404, detail=f"PR #{number} not found")
    return {"ok": True}


# Module-level registry of in-flight background sync threads. The
# `/api/review-requests/sync` route spawns a daemon thread and returns
# immediately; tests need a way to await that thread before teardown
# closes the DB connection (otherwise the bg thread's next
# `app_state._db` call hits a freed sqlite handle and segfaults --
# we caught this with a flake bisect on the full test suite).
_SYNC_THREADS: list = []


def _await_sync_threads(timeout: float = 5.0) -> int:
    """Join every still-running background sync thread. Called by the
    `patched_server` test fixture's teardown so a slow `/sync` call
    doesn't leak past the test that started it. Returns the count of
    threads we waited on. Production callers don't need this -- the
    threads are daemon so process exit kills them, and no
    production-side teardown is racing them."""
    waited = 0
    while _SYNC_THREADS:
        t = _SYNC_THREADS.pop()
        if t.is_alive():
            t.join(timeout=timeout)
            waited += 1
    return waited


@app_state.app.post("/api/review-requests/sync")
def sync_review_requests_route():
    """Trigger an async full sync of the review queue. Matches the
    `/api/all-prs/sync` contract: returns quickly with status, the
    heavy lifting happens on a worker thread so the UI isn't blocked.
    Frontend subscribes to `github.review.updated` to refetch when the
    refresh finishes."""
    import threading
    from common.prs import sync_review_requests as _sync

    def _runner():
        try:
            _sync()
        except Exception as e:
            print(f"[review-sync] failed: {e}", flush=True)

    t = threading.Thread(target=_runner, name="review-sync", daemon=True)
    _SYNC_THREADS.append(t)
    t.start()
    return {"status": "sync started"}


async def _fetch_pr_detail(pr_number, pr_url):
    """Fetch and update one PR's detail. Returns True if updated."""
    try:
        repo = repo_from_pr_url(pr_url)
        if not repo or not app_state.is_repo_allowed(repo):
            return False
        result = await app_state.gh_run_async(
            ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", _SYNC_VIEW_FIELDS],
            repo=repo, timeout=15,
        )
        if result.returncode != 0:
            return False
        item = _json.loads(result.stdout)
        gh_state = (item.get("state") or "").upper()
        # Check fork CI if this repo has a fork (the maintainer's
        # primary install routes acme/widgets PRs to a personal fork
        # for CI; open-source forks plug their own pairs into the
        # FORK_TO_UPSTREAM setting).
        # Overwrite `item["statusCheckRollup"]` in place so the shared
        # mapper below sees the fork-CI checks the same way the sync
        # path does via _update_pr_from_gh.
        fork_repo = _fork_ci_repos.get(repo)
        if fork_repo and gh_state == "OPEN":
            fork_jobs = await app_state.gh_run_async(
                ["gh", "run", "list", "--repo", fork_repo,
                 "--branch", item.get("headRefName", ""), "--workflow", "Build",
                 "--limit", "1", "--json", "databaseId,status,conclusion"],
                repo=fork_repo, timeout=10,
            )
            if fork_jobs.returncode == 0:
                runs = _json.loads(fork_jobs.stdout)
                if runs:
                    jobs_r = await app_state.gh_run_async(
                        ["gh", "run", "view", str(runs[0]["databaseId"]),
                         "--repo", fork_repo, "--json", "jobs",
                         "--jq", '.jobs[] | {name, conclusion, status}'],
                        repo=fork_repo, timeout=10,
                    )
                    if jobs_r.returncode == 0:
                        jobs = []
                        for line in jobs_r.stdout.strip().split("\n"):
                            if line.strip():
                                try:
                                    jobs.append(_json.loads(line))
                                except (ValueError, _json.JSONDecodeError):
                                    pass
                        if jobs:
                            item["statusCheckRollup"] = jobs

        # Delegate to the shared mapper (see common.prs.map_gh_pr_to_updates).
        # Keeps the three PR-refresh paths (this, sync generator, manual
        # refresh) perfectly consistent on field semantics + one-way
        # status_changed_at backfill.
        from common.prs import map_gh_pr_to_updates
        existing = app_state._db.find_pr_by_number(pr_number)
        update_kwargs = map_gh_pr_to_updates(item, repo, pr_number, existing)
        app_state._db.update_pr_by_number(pr_number, **update_kwargs)
        return True
    except Exception:
        return False


async def _discover_repo_async(repo_or_owner, author, *, limit="30"):
    """Async version of PR discovery for a single repo/owner.

    Returns a list of ``{"number": ..., "url": ...}`` dicts for newly-added PRs.
    Uses ``gh_run_async`` so it can be called concurrently via ``asyncio.gather``.
    """
    found = []
    search_arg, gh_repo_hint = _build_search_args(repo_or_owner)
    for state in ["open", "closed"]:
        try:
            r = await app_state.gh_run_async(
                _build_pr_search_cmd(search_arg, author, state, limit),
                repo=gh_repo_hint, timeout=20,
            )
            if r.returncode != 0:
                continue
            for item in _json.loads(r.stdout):
                added = _ingest_discovered_item(item, repo_or_owner, state)
                if added:
                    found.append({"number": added["number"], "url": added["url"]})
        except Exception:
            pass
    return found


@app_state.app.get("/api/all-prs/sync-stream")
async def sync_all_prs_stream(full: int = 0):
    """SSE stream: discover + update PRs with progress events."""
    async def generate():
        yield f"data: {_json.dumps({'phase': 'start', 'full': bool(full)})}\n\n"
        repo_authors = app_state._build_repo_authors()
        updated = 0

        dirty_prs = app_state._db.list_dirty_prs()
        yield f"data: {_json.dumps({'phase': 'dirty', 'count': len(dirty_prs)})}\n\n"

        batch_size = 6
        for i in range(0, len(dirty_prs), batch_size):
            batch = dirty_prs[i:i + batch_size]
            tasks = [_fetch_pr_detail(p["number"], p.get("url", "")) for p in batch]
            results = await asyncio.gather(*tasks)
            for j, ok in enumerate(results):
                if ok:
                    app_state._db.clear_pr_dirty(batch[j]["number"])
                    updated += 1
            yield f"data: {_json.dumps({'phase': 'dirty_update', 'current': min(i + batch_size, len(dirty_prs)), 'total': len(dirty_prs)})}\n\n"

        limit = "200" if full else "30"
        discover_tasks = [_discover_repo_async(repo, author, limit=limit) for repo, author in repo_authors.items()]
        discover_results = await asyncio.gather(*discover_tasks)
        newly_discovered = []
        discovered = 0
        for found in discover_results:
            newly_discovered.extend(found)
            discovered += len(found)

        yield f"data: {_json.dumps({'phase': 'discover', 'discovered': discovered})}\n\n"

        detail_prs = app_state._db.list_all_prs() if full else newly_discovered
        total = len(detail_prs)

        for i in range(0, max(total, 1), batch_size):
            batch = detail_prs[i:i + batch_size]
            tasks = [_fetch_pr_detail(p.get("number"), p.get("url", "")) for p in batch]
            results = await asyncio.gather(*tasks)
            updated += sum(1 for ok in results if ok)
            yield f"data: {_json.dumps({'phase': 'update', 'current': min(i + batch_size, total), 'total': total, 'updated': updated})}\n\n"

        yield f"data: {_json.dumps({'phase': 'done', 'discovered': discovered, 'updated': updated, 'total': total})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _discover_new_prs(repo_authors, *, limit="200"):
    """Search GitHub for PRs and add any that are not yet tracked.

    Returns ``(discovered_count, discovered_prs, errors)`` where
    *discovered_prs* is a list of ``{"number": ..., "url": ...}`` dicts
    for each newly-added PR.
    """
    discovered = 0
    discovered_prs = []
    errors = []

    for repo_or_owner, author in repo_authors.items():
        search_arg, gh_repo_hint = _build_search_args(repo_or_owner)
        for state in ["open", "closed"]:
            try:
                items = app_state.gh_run_json(
                    _build_pr_search_cmd(search_arg, author, state, limit),
                    repo=gh_repo_hint, timeout=20,
                )
                if items is None:
                    continue
                for item in items:
                    added = _ingest_discovered_item(item, repo_or_owner, state)
                    if added:
                        discovered += 1
                        discovered_prs.append(
                            {"number": added["number"], "url": added["url"]}
                        )
                        print(
                            "[sync] Discovered PR #%s in %s -> %s/%s"
                            % (added["number"], added["repo"],
                               added["match"][0], added["match"][1]),
                            flush=True,
                        )
            except Exception as e:
                errors.append(f"discover {repo_or_owner}/{state}: {str(e)[:60]}")

    return discovered, discovered_prs, errors


@app_state.app.post("/api/all-prs/sync")
def sync_all_prs():
    """Discover new PRs from GitHub and update existing ones."""
    repo_authors = app_state._build_repo_authors()

    discovered, _new_prs, errors = _discover_new_prs(repo_authors)

    all_prs = app_state._db.list_all_prs()
    updated = 0

    for pr in all_prs:
        pr_number = pr.get("number")
        pr_url = pr.get("url", "")
        if not pr_number or not pr_url:
            continue
        try:
            repo = repo_from_pr_url(pr_url)
        except Exception:
            continue
        if not repo or not app_state.is_repo_allowed(repo):
            continue
        try:
            item = app_state.gh_run_json(
                ["gh", "pr", "view", str(pr_number), "--repo", repo, "--json", _SYNC_VIEW_FIELDS],
                repo=repo, timeout=15,
            )
            if item is None:
                continue
            gh_state = (item.get("state") or "").upper()
            changes = {
                "status": _resolve_pr_status(gh_state, repo, pr_number),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "ci_status": _aggregate_ci_status(
                    _fetch_fork_ci(item.get("headRefName", "")) or item.get("statusCheckRollup") or []
                    if _fork_ci_repos.get(repo) and gh_state == "OPEN"
                    else item.get("statusCheckRollup") or []),
                "review_status": (item.get("reviewDecision") or "").lower(),
                "comment_count": len(item.get("comments", []) or []) + len(item.get("reviews", []) or []),
                "additions": item.get("additions", 0) or 0,
                "deletions": item.get("deletions", 0) or 0,
                "author": (item.get("author", {}) or {}).get("login", ""),
                "head_branch": item.get("headRefName", ""),
                "base_branch": item.get("baseRefName", ""),
                "last_updated": item.get("updatedAt", ""),
            }
            app_state._db.update_pr_by_number(pr_number, **changes)
            updated += 1
        except Exception as e:
            errors.append(f"PR #{pr_number}: {str(e)[:60]}")

    return {"discovered": discovered, "updated": updated, "total": len(all_prs) + discovered, "errors": errors}


@app_state.app.get("/api/pr-detail")
def get_pr_detail(repo: str, number: int):
    """Fetch PR detail from GitHub using gh CLI."""
    pr_data = _get_pr_detail_core(repo, number)
    if pr_data is None:
        raise HTTPException(status_code=404, detail="PR not found")
    return pr_data


class PRCreate(BaseModel):
    number: int
    url: str
    status: str = "open"
    title: str = ""
    session: Optional[str] = None
    working_dir: str = "~"
    agent_args: str = ""


@app_state.app.post("/api/projects/{project_id}/tasks/{task_id}/prs", status_code=201)
def add_pr(project_id: str, task_id: str, body: PRCreate):
    """Add a PR to a task."""
    try:
        result = _add_pr(project_id, task_id, number=body.number, url=body.url,
                         status=body.status, title=body.title,
                         session=body.session, working_dir=body.working_dir)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="PR already exists")
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    return body.model_dump()


@app_state.app.delete("/api/projects/{project_id}/tasks/{task_id}/prs/{pr_number}", status_code=204)
def delete_pr(project_id: str, task_id: str, pr_number: int):
    """Remove a PR from a task."""
    result = _remove_pr(project_id, task_id, pr_number)
    if result is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if result is False:
        raise HTTPException(status_code=404, detail="PR not found")


class PRComment(BaseModel):
    repo: str
    number: int
    body: str


@app_state.app.post("/api/pr-comment")
def post_pr_comment(body: PRComment):
    """Post a comment on a PR using gh CLI."""
    app_state.gh_run_or_raise(
        ["gh", "pr", "comment", str(body.number), "--repo", body.repo, "--body", body.body],
        repo=body.repo)
    return {"ok": True}


class PRBodyUpdate(BaseModel):
    repo: str
    number: int
    body: str


@app_state.app.post("/api/pr-body")
def update_pr_body(payload: PRBodyUpdate):
    """Update the body/description of a PR using gh CLI."""
    app_state.gh_run_or_raise(
        ["gh", "pr", "edit", str(payload.number), "--repo", payload.repo, "--body", payload.body],
        repo=payload.repo)
    return {"ok": True}


class PRTitleUpdate(BaseModel):
    repo: str
    number: int
    title: str


@app_state.app.post("/api/pr-title")
def update_pr_title(payload: PRTitleUpdate):
    """Update the title of a PR using gh CLI."""
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Title cannot be empty")
    app_state.gh_run_or_raise(
        ["gh", "pr", "edit", str(payload.number), "--repo", payload.repo, "--title", payload.title.strip()],
        repo=payload.repo)
    # Also update title in DB if PR is tracked
    pr_row = app_state._db.find_pr_by_number(payload.number)
    if pr_row:
        app_state._db.update_pr_by_number(payload.number, title=payload.title.strip())
    return {"ok": True}


@app_state.app.get("/api/pr-diff")
def get_pr_diff(repo: str, number: int):
    """Fetch the unified diff for a PR using gh CLI. Returns per-file hunks."""
    result = app_state.gh_run_or_raise(
        ["gh", "pr", "diff", str(number), "--repo", repo],
        repo=repo, timeout=30, stderr_limit=200,
    )

    # Parse unified diff into per-file sections
    raw = result.stdout
    files = {}
    current_file = None
    current_lines = []

    for line in raw.split("\n"):
        if line.startswith("diff --git"):
            if current_file:
                files[current_file] = "\n".join(current_lines)
            # Extract filename: "diff --git a/path b/path"
            parts = line.split(" b/", 1)
            current_file = parts[1] if len(parts) > 1 else line
            current_lines = [line]
        elif current_file is not None:
            current_lines.append(line)

    if current_file:
        files[current_file] = "\n".join(current_lines)

    return {"files": files}


@app_state.app.get("/api/pr-lookup/{number}")
def lookup_pr_by_number(number: int):
    """Look up PR by number, returns project, task_id, and url.

    `url` is included so the frontend can derive the full `org/repo`
    from a stale URL state that only carries a bare repo name (e.g.
    `pr_repo=widgets`) -- without this lookup the PR detail fetch
    would 404 because gh CLI needs the full `acme/widgets` form.
    """
    pr = app_state._db.find_pr_by_number(number)
    if not pr:
        return {"found": False}
    return {
        "found": True,
        "project": pr["project"], "task_id": pr["task_id"],
        "number": number,
        "url": pr.get("url", ""),
    }


# Fields whose change marks a PR as "actually changed" (worth firing
# `github.pr.updated`). Cosmetic / time-only updates (`last_updated`,
# `status_changed_at`) are intentionally excluded so a no-op refresh
# from the scheduled poller doesn't flood the SSE bus.
_PR_CHANGE_DETECT_FIELDS = (
    "status", "title", "ci_status", "review_status",
    "comment_count", "additions", "deletions", "author",
)


def _compute_pr_changes(
    pr: dict, repo: str, number: int, detail: dict,
) -> tuple[dict, bool]:
    """Pure mapper: gh `pr view` detail + current row -> (changes dict,
    was_meaningfully_changed bool).

    The `changes` dict is what we'll pass to `update_pr_by_number`.
    `was_meaningfully_changed` is True when any field in
    `_PR_CHANGE_DETECT_FIELDS` differs -- timestamp-only updates don't
    fire the change event.
    """
    from common.prs import _aggregate_ci_status
    from common.prs import _status_change_at as _sca

    changes: dict = {}
    if detail.get("state"):
        changes["status"] = _resolve_pr_status(detail["state"].upper(),
                                                repo, number)
    if detail.get("title"):
        changes["title"] = detail["title"]
    # Canonical aggregator: failure > pending > success > unknown.
    # The inline "any FAILURE -> failure else success" used to mark
    # IN_PROGRESS / QUEUED / PENDING as success.
    changes["ci_status"] = _aggregate_ci_status(
        detail.get("statusCheckRollup") or [],
    )
    # Canonical lowercasing matches the rest of the codebase. The old
    # mapper only translated APPROVED / CHANGES_REQUESTED and silently
    # dropped REVIEW_REQUIRED into "".
    changes["review_status"] = (detail.get("reviewDecision") or "").lower()
    changes["comment_count"] = (
        len(detail.get("comments") or [])
        + len(detail.get("reviews") or [])
    )
    changes["additions"] = detail.get("additions", 0)
    changes["deletions"] = detail.get("deletions", 0)
    # Backfill author from gh. Workstats filters PRs to those authored
    # by my gh logins; an empty author column makes the row invisible
    # in the chart. Refresh used to skip this field, so PRs added
    # before the auto-author code path stayed empty forever.
    login = ((detail.get("author") or {}).get("login") or "").strip()
    if login:
        changes["author"] = login

    # Detect change BEFORE adding the cosmetic timestamp fields.
    was_changed = any(
        (pr.get(k) if pr is not None else None) != changes.get(k)
        for k in _PR_CHANGE_DETECT_FIELDS
        if k in changes
    )

    # `last_updated` mirrors GitHub's own `updatedAt`. Stamping it to
    # now() used to make every refreshed PR look like it was touched
    # seconds ago. When GH didn't return an updatedAt (rare, usually
    # archived/closed) keep whatever we had.
    gh_updated = (detail.get("updatedAt") or "").strip()
    if gh_updated:
        changes["last_updated"] = gh_updated
    # Backfill `status_changed_at` from mergedAt/closedAt when we
    # don't already have a value. See core/common.prs.py::_status_change_at.
    changed_at = _sca(detail)
    if changed_at and not (pr.get("status_changed_at") or "").strip():
        changes["status_changed_at"] = changed_at

    return changes, was_changed


@app_state.app.post("/api/pr-refresh/{number}")
def refresh_single_pr(number: int):
    """Fetch one PR from GitHub now, then emit `github.pr.updated` so
    listening UI components (SessionsPage, ProjectPage, PRsPage) refetch
    instead of holding the stale state they already loaded.

    Also clears the dirty flag if set (the scheduled sync won't need to
    redo this PR right after we manually refreshed it).
    """
    pr = app_state._db.find_pr_by_number(number)
    if not pr:
        return {"ok": True, "changed": False, "fetched": False}

    from common.prs import get_pr_detail
    url = pr.get("url", "")
    repo = repo_from_pr_url(url)
    if not repo:
        app_state._db.clear_pr_dirty(number)
        return {"ok": True, "changed": False, "fetched": False}

    detail = get_pr_detail(repo, number)
    if not detail:
        app_state._db.clear_pr_dirty(number)
        return {"ok": True, "changed": False, "fetched": False}

    changes, changed = _compute_pr_changes(pr, repo, number, detail)
    app_state._db.update_pr_by_number(number, **changes)
    app_state._db.clear_pr_dirty(number)
    pr_after = {**pr, **changes}

    if changed:
        # `github.pr.updated` is the canonical event for PR-state
        # changes (manual refresh, scheduled poller, webhook). Frontend
        # subscribes via useEventBus('github.*'). persist=False -- this
        # is a silent state sync, not user-visible news.
        app_state.emit_event("github.pr.updated", {
            "title": f"PR #{number} updated",
            "message": (f"status={pr_after.get('status')} "
                        f"ci={pr_after.get('ci_status')} "
                        f"review={pr_after.get('review_status')}"),
            "pr_number": number,
            "project": pr_after.get("project"),
            "task_id": pr_after.get("task_id"),
        }, persist=False)

    return {"ok": True, "changed": changed, "fetched": True}


class CommentReply(BaseModel):
    repo: str
    number: int
    comment_id: int
    body: str
    is_review_comment: bool = False


@app_state.app.post("/api/pr-comment-reply")
def reply_to_comment(payload: CommentReply):
    """Reply to a PR comment (general or review/inline) via REST API."""
    org_repo = payload.repo
    if payload.is_review_comment:
        # Reply to a review (inline) comment
        app_state.gh_run_or_raise(
            ["gh", "api", f"repos/{org_repo}/pulls/{payload.number}/comments",
             "-f", f"body={payload.body}",
             "-F", f"in_reply_to={payload.comment_id}",
             "--method", "POST"],
            repo=org_repo)
    else:
        # Reply to an issue (general) comment -- just post a new issue comment
        app_state.gh_run_or_raise(
            ["gh", "api", f"repos/{org_repo}/issues/{payload.number}/comments",
             "-f", f"body={payload.body}",
             "--method", "POST"],
            repo=org_repo)
    return {"ok": True}


class CommentEdit(BaseModel):
    repo: str
    comment_id: int
    body: str
    is_review_comment: bool = False


@app_state.app.post("/api/pr-comment-edit")
def edit_comment(payload: CommentEdit):
    """Edit a PR comment (general or review/inline) via REST API."""
    org_repo = payload.repo
    if payload.is_review_comment:
        app_state.gh_run_or_raise(
            ["gh", "api", f"repos/{org_repo}/pulls/comments/{payload.comment_id}",
             "-f", f"body={payload.body}",
             "--method", "PATCH"],
            repo=org_repo)
    else:
        app_state.gh_run_or_raise(
            ["gh", "api", f"repos/{org_repo}/issues/comments/{payload.comment_id}",
             "-f", f"body={payload.body}",
             "--method", "PATCH"],
            repo=org_repo)
    return {"ok": True}


class ThreadResolve(BaseModel):
    thread_id: str
    resolve: bool = True
    repo: str = ""


@app_state.app.post("/api/pr-thread-resolve")
def resolve_thread(payload: ThreadResolve):
    """Resolve or unresolve a review thread via GraphQL mutation."""
    mutation = "resolveReviewThread" if payload.resolve else "unresolveReviewThread"
    gql = 'mutation { %s(input: {threadId: "%s"}) { thread { id isResolved } } }' % (
        mutation, payload.thread_id)
    app_state.gh_run_or_raise(
        ["gh", "api", "graphql", "-f", "query=" + gql],
        repo=payload.repo)
    return {"ok": True}


class PRReviewSubmit(BaseModel):
    repo: str
    number: int
    event: str        # APPROVE | REQUEST_CHANGES | COMMENT
    body: str = ""    # overall review body, optional for APPROVE


_REVIEW_EVENTS = {"APPROVE", "REQUEST_CHANGES", "COMMENT"}


@app_state.app.post("/api/pr-review")
def submit_pr_review(payload: PRReviewSubmit):
    """Submit a full PR review (Approve / Request changes / Comment),
    matching GitHub's "Review changes" dialog.

    Uses `POST /repos/{owner}/{repo}/pulls/{number}/reviews`. `event`
    must be one of APPROVE, REQUEST_CHANGES, COMMENT (GitHub's API
    spelling). Body is optional for APPROVE, required for the other
    two -- GitHub rejects an empty REQUEST_CHANGES/COMMENT review.

    Also marks the PR dirty in the review queue + tasks DB so the next
    poll picks up the new review_status.
    """
    event = (payload.event or "").upper()
    if event not in _REVIEW_EVENTS:
        raise HTTPException(
            status_code=422,
            detail=f"event must be one of {sorted(_REVIEW_EVENTS)}; got {payload.event!r}",
        )
    body = (payload.body or "").strip()
    if event != "APPROVE" and not body:
        raise HTTPException(
            status_code=422,
            detail=f"body is required when event={event}",
        )
    args = [
        "gh", "api",
        f"repos/{payload.repo}/pulls/{payload.number}/reviews",
        "--method", "POST",
        "-f", f"event={event}",
    ]
    # Only attach body when non-empty -- GitHub defaults it to "" when
    # the key is omitted, which is fine for APPROVE.
    if body:
        args += ["-f", f"body={body}"]
    app_state.gh_run_or_raise(args, repo=payload.repo)
    # Optimistic local state: bump `my_review_state` on the review_prs
    # row (if any) so the queue's pill updates without waiting for the
    # next sync cycle. Each event maps 1:1 to a persisted state.
    _event_to_state = {
        "APPROVE": "approved",
        "REQUEST_CHANGES": "changes_requested",
        "COMMENT": "commented",
    }
    canonical = f"https://github.com/{payload.repo}/pull/{payload.number}"
    existing = app_state._db.get_review_pr(canonical)
    if existing:
        app_state._db.upsert_review_pr(
            url=canonical,
            repo=existing["repo"],
            number=existing["number"],
            my_review_state=_event_to_state[event],
        )
    # Invalidate: next poll will pull fresh review_status (in case my
    # submission changed the aggregate reviewDecision, too).
    app_state._db.mark_pr_dirty(payload.number)
    app_state._db.mark_review_pr_dirty(number=payload.number)
    app_state.emit_event("github.pr.updated", {
        "title": f"Review {event.lower().replace('_', ' ')} submitted on #{payload.number}",
        "message": (body[:200] or event),
        "severity": "info",
        "pr_number": payload.number,
    }, persist=False)
    return {"ok": True, "event": event}
