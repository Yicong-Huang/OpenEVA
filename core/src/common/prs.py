"""PR management core logic."""

import json as _json
import time as _time

import app_state
import pr_sync as _pr
from utils import repo_from_pr_url


# -- PR info cache --

_pr_info_cache = {}


def list_all_prs(status="", search=""):
    """Return all PRs grouped by project. Hidden projects (per
    `ui.hidden_projects`) are skipped so the All-PRs view stays
    consistent with the sidebar."""
    from . import settings as _settings
    hidden = _settings.get_hidden_projects()
    prs = app_state._db.list_all_prs(status=status, search=search)

    proj_names = app_state.project_name_map()

    grouped = {}
    for pr in prs:
        pid = pr.get("project", "other")
        if pid in hidden:
            continue
        if pid not in grouped:
            grouped[pid] = {"name": proj_names.get(pid, pid), "prs": []}
        grouped[pid]["prs"].append(pr)

    return {"groups": grouped}


# Accounts we poll for review-requested PRs. Each hint string is a repo
# path (or org) that `gh_account_for_repo` maps to the right token --
# the hint itself isn't queried, it only picks the account. The
# module-level tuple is empty by default so no maintainer-specific repo
# names are baked into the source; `_review_account_hints()` derives
# defaults from the configured `ALLOWED_REPOS` (which itself is
# DB-driven) when neither the override setting nor this constant are
# populated. Tests still monkeypatch this constant directly via
# `monkeypatch.setattr` so its presence preserves the test surface.
_REVIEW_ACCOUNT_HINTS: tuple[str, ...] = ()


def _review_account_hints() -> tuple[str, ...]:
    """Return the hints to iterate over.

    Resolution order (first non-empty wins):
      1. `service.github.review_account_hints` setting (list of strings).
      2. The module-level `_REVIEW_ACCOUNT_HINTS` constant (tests
         monkeypatch this).
      3. Derived from `adapters.github.ALLOWED_REPOS`: explicit
         `org/repo` entries are kept verbatim; org wildcards
         (`acme/*`) are reduced to the org so token selection still
         works.

    Returns an empty tuple if no source is configured -- in that case
    the review-queue refresh runs zero searches, which is the right
    behaviour for a fresh OSS install with no repos configured yet.
    """
    try:
        from . import settings as _settings
        v = _settings.get_value(
            "service.github.review_account_hints", default=None)
        if isinstance(v, list) and v:
            return tuple(str(x) for x in v if isinstance(x, str))
    except Exception:
        pass
    if _REVIEW_ACCOUNT_HINTS:
        return _REVIEW_ACCOUNT_HINTS
    try:
        from adapters import github as _gh
        derived: list[str] = []
        for repo in sorted(_gh.ALLOWED_REPOS):
            if repo.endswith("/*"):
                derived.append(repo[:-2])  # "acme/*" -> "acme"
            elif "/" in repo:
                derived.append(repo)
        return tuple(derived)
    except Exception:
        return ()


def _my_gh_logins() -> set[str]:
    """Set of my GitHub usernames (case-insensitive) taken from the
    loaded `gh` tokens -- same source `gh_account_for_repo` uses so
    there's a single source of truth. Empty set if the hosts.yml
    couldn't be parsed (review queue then errs on the side of showing
    everything)."""
    return {u.lower() for u in app_state._gh_tokens.keys() if u}


# Fields we pull from `gh pr view` to populate the per-PR card status
# (CI ring, review decision pill, diff stats, branches, my own review
# state). Kept tight so the review-queue enrichment isn't too chatty
# per PR. `latestReviews` gives us one review per reviewer (the most
# recent), which is exactly what we need to decide "what did *I* say".
# `reviewRequests` is the list of users/teams currently pinged for
# review -- if I'm in it, my state resets to pending_review even if I
# previously reviewed (that's how re-request works).
# Includes every field the all-PRs mapper (`map_gh_pr_to_updates`)
# reads, so review-queue enrichment can reuse the same status/CI/diff
# update path the `prs` table uses. Without `state`, a manual-pinned
# PR that gets merged on GitHub never flips out of 'open' in the
# review queue (the original sync only refreshed CI / review / diff
# fields and silently left status stale forever).
_REVIEW_ENRICH_FIELDS = (
    "state,title,author,url,updatedAt,"
    "statusCheckRollup,reviewDecision,comments,reviews,latestReviews,"
    "reviewRequests,additions,deletions,headRefName,baseRefName"
)


# Per-reviewer states we map to. Mirrors the sqlite CHECK constraint
# on `review_prs.my_review_state` (see eva_db.EvaDB.REVIEW_STATES).
REVIEW_STATE_NONE = ""
REVIEW_STATE_PENDING = "pending_review"
REVIEW_STATE_APPROVED = "approved"
REVIEW_STATE_CHANGES_REQUESTED = "changes_requested"
REVIEW_STATE_COMMENTED = "commented"

# GitHub review `state` string -> our enum. Dismissed / pending drop
# to NONE because they don't represent an active stance.
_GH_REVIEW_STATE_MAP = {
    "APPROVED": REVIEW_STATE_APPROVED,
    "CHANGES_REQUESTED": REVIEW_STATE_CHANGES_REQUESTED,
    "COMMENTED": REVIEW_STATE_COMMENTED,
}


def _compute_my_review_state(detail: dict, my_logins: set[str]) -> str:
    """Decide the "my review" pill value for one PR.

    Priority: `pending_review` if GitHub currently has me in the
    requested-reviewers list (covers both fresh requests and
    re-requests after I already reviewed). Otherwise, use the latest
    review authored by me. Empty when I'm not involved.
    """
    if not my_logins:
        return REVIEW_STATE_NONE
    requested = detail.get("reviewRequests") or []
    for r in requested:
        login = (r or {}).get("login", "")
        if login and login.lower() in my_logins:
            return REVIEW_STATE_PENDING
    for r in (detail.get("latestReviews") or []):
        login = ((r or {}).get("author") or {}).get("login", "")
        if not login or login.lower() not in my_logins:
            continue
        mapped = _GH_REVIEW_STATE_MAP.get((r.get("state") or "").upper())
        if mapped:
            return mapped
    return REVIEW_STATE_NONE




def _row_to_pr(row: dict) -> dict:
    """Shape a `review_prs` row for the frontend (PR interface).

    Passes through the reviewer-workflow columns (session_name /
    agent_session_id / my_workflow_state / started_at) so ReviewCard
    can render the inline SessionCard and the state-toggle buttons.
    Without these, the ReviewCard never knows a session was started.
    """
    comment_count = row.get("comment_count", 0) or 0
    last_seen = row.get("last_seen_comment_count", 0) or 0
    return {
        "number": row.get("number"),
        "title": row.get("title", ""),
        "url": row.get("url", ""),
        "status": row.get("status") or "open",
        "author": row.get("author", ""),
        "last_updated": row.get("last_updated", ""),
        "repo": row.get("repo", ""),
        "source": row.get("source", "manual"),
        "ci_status": row.get("ci_status") or "unknown",
        "review_status": row.get("review_status") or "review_required",
        "my_review_state": row.get("my_review_state") or "",
        "comment_count": comment_count,
        # Computed by the server so each frontend doesn't have to
        # repeat the max(0, ...) clamp. Drives the "N new" badge on
        # the PR node when > 0.
        "unread_comment_count": max(0, comment_count - last_seen),
        "additions": row.get("additions", 0) or 0,
        "deletions": row.get("deletions", 0) or 0,
        "head_branch": row.get("head_branch", "") or "",
        "base_branch": row.get("base_branch", "") or "",
        "session_name": row.get("session_name", "") or "",
        "agent_session_id": row.get("agent_session_id", "") or "",
        "my_workflow_state": row.get("my_workflow_state") or "queued",
        "started_at": row.get("started_at", "") or "",
    }


_REVIEW_SEARCH_FIELDS = "number,title,url,state,author,updatedAt,repository"


def _fetch_review_buckets(my_logins: set, limit: int) -> tuple[dict, dict]:
    """Shell out to `gh search prs --review-requested=@me` and
    `--mentions=@me` for every account hint, and return two disjoint
    url->data dicts ready for upsert.

    review-requested is the hard "you MUST review" bucket; mentions is
    the softer "someone pinged you" bucket that gets treated like a
    manual pin. PRs authored by `my_logins` are filtered out.
    """
    review_requested: dict = {}
    mentions: dict = {}
    per_query = (
        ("--review-requested=@me", review_requested),
        ("--mentions=@me", mentions),
    )
    for hint in _review_account_hints():
        for search_flag, bucket in per_query:
            cmd = [
                "gh", "search", "prs", search_flag,
                "--state=open",
                "--limit", str(limit),
                "--json", _REVIEW_SEARCH_FIELDS,
            ]
            items = app_state.gh_run_json(cmd, repo=hint, timeout=20, default=[]) or []
            for item in items:
                repo = (item.get("repository") or {}).get("nameWithOwner", "")
                number = item.get("number")
                if not repo or not number:
                    continue
                author = (item.get("author") or {}).get("login", "")
                if author and author.lower() in my_logins:
                    continue
                url = item.get("url") or f"https://github.com/{repo}/pull/{number}"
                bucket.setdefault(url, {
                    "repo": repo,
                    "number": number,
                    "title": (item.get("title") or "").strip(),
                    "author": author,
                    "status": (item.get("state") or "open").lower(),
                    "last_updated": item.get("updatedAt", ""),
                })
    return review_requested, mentions


def _resolve_review_source(is_req: bool, prior_source: str) -> tuple[str, int]:
    """Pure transition rule: given "did the fresh sync see this URL as
    review-requested?" and "what was already stored?", return the
    `source` enum to persist, plus 1 when this counts as a
    manual->both promotion (for the caller's counter).

    Extracted so the write-source decision is unit-testable without
    touching the DB or `gh`.
    """
    if is_req and prior_source in ("manual", "both"):
        return ("both", 1)
    if is_req:
        return ("github", 0)
    if prior_source in ("manual", "both", "github"):
        # Mention-only sync shouldn't clobber a more meaningful prior
        # classification.
        return (prior_source, 0)
    return ("manual", 0)


def _prune_stale_review_rows(fresh_req: set) -> int:
    """Delete `source='github'` rows whose URL dropped out of this
    pass's review-requested results. Downgrade `source='both'` rows
    to `source='manual'` so the user's pin survives even though GitHub
    no longer considers it review-requested. Returns the delete count."""
    removed = 0
    for row in app_state._db.list_review_prs():
        url = row["url"]
        if row["source"] == "github" and url not in fresh_req:
            app_state._db.delete_review_pr(url)
            removed += 1
        elif row["source"] == "both" and url not in fresh_req:
            app_state._db.upsert_review_pr(
                url=url, repo=row["repo"], number=row["number"],
                source="manual",
            )
    return removed


def sync_review_requests(limit: int | None = None) -> dict:
    """Pull everything GitHub knows about my review queue (both
    `--review-requested=@me` and `--mentions=@me` across both
    accounts) plus enrichment from `gh pr view`, and upsert it all
    into the `review_prs` table.

    `limit` is the per-search `--limit` passed to `gh search prs`.
    When unset, falls back to the configurable
    `ui.reviews.sync_search_limit` setting (default 50, bounds
    [10, 200]). Explicit callers (tests) can still pin it.

    Returns counts: `{"github": int, "manual_promoted": int,
    "removed_stale": int}`.

    Semantics:
      * GitHub-discovered PRs start as source="github". If a manual
        pin already covers the same URL, source gets bumped to "both".
      * PRs that dropped off GitHub's review list (merged, un-assigned,
        closed) and weren't manually pinned get deleted from the table
        so the queue shrinks.
      * My own PRs are never written.
      * A per-PR `gh pr view` enrichment fills CI / review / diff /
        branch fields concurrently.
    """
    if limit is None:
        from . import settings as _settings
        limit = _settings.get_reviews_sync_search_limit()
    my_logins = _my_gh_logins()
    review_requested, mentions = _fetch_review_buckets(my_logins, limit)

    # Merge for enrichment -- each URL needs `gh pr view` exactly once.
    all_rows: dict = {url: dict(data) for url, data in review_requested.items()}
    for url, data in mentions.items():
        all_rows.setdefault(url, dict(data))
    # Pull in any manual-pinned rows that GitHub didn't surface this
    # pass. Without this they'd never get re-enriched and their
    # status / CI / review fields would freeze at whatever they were
    # at pin time -- e.g. a manually-pinned PR that gets merged
    # upstream would stay status='open' in the queue forever.
    for row in app_state._db.list_review_prs():
        url = row["url"]
        if url in all_rows:
            continue
        if row.get("source") not in ("manual", "both"):
            continue
        repo = row.get("repo") or ""
        number = row.get("number") or 0
        if not repo or not number:
            continue
        all_rows[url] = {
            "repo": repo,
            "number": number,
            "title": row.get("title", "") or "",
            "author": row.get("author", "") or "",
            # Will be overwritten by enrichment with the real GitHub
            # state -- the seed values here just keep the dict shape
            # consistent with the review_requested / mentions buckets.
            "status": row.get("status") or "open",
            "last_updated": row.get("last_updated", "") or "",
        }
    _enrich_review_rows(all_rows)

    # Upsert with the right source (transition rules in _resolve_review_source).
    promoted = 0
    for url, data in all_rows.items():
        existing = app_state._db.get_review_pr(url)
        prior_source = existing["source"] if existing else ""
        source, promo_delta = _resolve_review_source(
            is_req=url in review_requested,
            prior_source=prior_source,
        )
        promoted += promo_delta
        app_state._db.upsert_review_pr(url=url, source=source, **data)

    removed = _prune_stale_review_rows(set(review_requested.keys()))

    # All rows just written got fresh enrichment -- nothing else is
    # still dirty at this point. Clear the pins so the dirty-only worker
    # doesn't waste another gh call on something we already refreshed.
    app_state._db.clear_all_review_pr_dirty()

    # Emit so the frontend can auto-refetch the queue view without
    # waiting for the user to click Refresh. `persist=False` keeps
    # this out of the notifications feed (it's a silent state sync).
    app_state.emit_event("github.review.updated", {
        "title": "Review queue refreshed",
        "message": (
            f"{len(review_requested)} review-requested, "
            f"{len(mentions)} mentions, {removed} stale"
        ),
        "severity": "info",
        "source_id": "review-full-sync",
    }, persist=False)
    return {
        "github": len(review_requested),
        "mentions": len(mentions),
        "manual_promoted": promoted,
        "removed_stale": removed,
    }


def _enrich_review_rows(rows: dict) -> None:
    """Fan out `gh pr view` across the working rows dict (url -> data).
    Mutates each row dict in place with ci/review/diff/branch +
    my_review_state fields."""
    if not rows:
        return

    # Snapshot my_logins once for the whole batch -- the underlying set
    # doesn't change during one sync pass, and each _one() iteration
    # would otherwise re-read `app_state._gh_tokens` needlessly.
    my_logins = _my_gh_logins()

    def _one(item):
        url, data = item
        try:
            detail = app_state.gh_run_json(
                ["gh", "pr", "view", str(data["number"]),
                 "--repo", data["repo"],
                 "--json", _REVIEW_ENRICH_FIELDS],
                repo=data["repo"], timeout=15, default={},
            )
        except Exception:
            return
        if not isinstance(detail, dict):
            return
        # Reuse the all-PRs mapper so review_prs.status (plus CI / diff
        # / branches / last_updated) updates exactly the same way the
        # `prs` table does. `existing` lets the mapper preserve a once-
        # backfilled status_changed_at across polls.
        existing = app_state._db.get_review_pr(url)
        updates = map_gh_pr_to_updates(detail, data["repo"], data["number"], existing)
        # `url` is provided separately to the upsert call -- keeping
        # it in `data` would crash with "multiple values for keyword
        # argument 'url'".
        updates.pop("url", None)
        data.update(updates)
        # my_review_state is review-queue-specific (not in the all-PRs
        # mapper), so compute it separately.
        data["my_review_state"] = _compute_my_review_state(detail, my_logins)

    import concurrent.futures as _cf
    max_workers = min(6, len(rows))
    with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
        list(ex.map(_one, rows.items()))


def list_review_requests() -> list:
    """Return the persisted review queue -- pure DB read, no gh calls.

    Matches the `/api/all-prs` pattern: the UI gets instant results
    from the table, and `sync_review_requests()` runs separately
    (scheduler + manual trigger via `/api/review-requests/sync`).

    Each row carries `session_name` so the frontend can index it into
    the global session-status snapshot. We no longer attach
    `session_alive` / `session_status` per row -- the snapshot
    service (`/api/sessions/snapshot` + SSE patches) is authoritative.
    """
    my_logins = _my_gh_logins()
    out = []
    for row in app_state._db.list_review_prs():
        # Belt-and-braces author filter against legacy rows written
        # before the "skip my own PRs" guard existed.
        if (row.get("author") or "").lower() in my_logins:
            continue
        out.append(_row_to_pr(row))
    return out


def sync_review_requests_dirty_only() -> dict:
    """Refresh only rows flagged `dirty=1`. Used by the fast scheduler
    cadence so notification-driven updates land quickly without doing
    the full `gh search` sweep.

    Emits `github.review.updated` when at least one row actually
    changed so the frontend can refetch without the user clicking
    Refresh.
    """
    dirty = app_state._db.list_dirty_review_prs()
    if not dirty:
        return {"refreshed": 0}
    # Build the enrichment working set. `_enrich_review_rows` reads
    # `repo`/`number` and writes the CI/review/diff/branch fields back
    # in place. We strip repo/number before handing the dict to the
    # upsert (positional args) to avoid duplicate-kwarg errors.
    working = {row["url"]: {
        "repo": row["repo"],
        "number": row["number"],
        "title": row.get("title", ""),
        "author": row.get("author", ""),
        "status": row.get("status", "open"),
        "last_updated": row.get("last_updated", ""),
    } for row in dirty}
    _enrich_review_rows(working)
    refreshed = 0
    for row in dirty:
        url = row["url"]
        data = dict(working[url])
        data.pop("repo", None)
        data.pop("number", None)
        app_state._db.upsert_review_pr(
            url=url, repo=row["repo"], number=row["number"],
            source=row.get("source", "manual"),
            **data,
        )
        app_state._db.clear_review_pr_dirty(url)
        refreshed += 1
    if refreshed:
        app_state.emit_event("github.review.updated", {
            "title": f"Review queue refreshed ({refreshed})",
            "message": f"{refreshed} PR(s) updated",
            "severity": "info",
            "source_id": "review-dirty-sync",
        }, persist=False)
    return {"refreshed": refreshed}


def _parse_pr_url(url: str) -> tuple[str, int] | None:
    """Extract (repo, number) from a GitHub PR URL. Returns None on any
    malformed input so the caller can 422 cleanly."""
    import re as _re
    m = _re.match(r"^https?://github\.com/([^/]+/[^/]+)/pull/(\d+)", (url or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def add_review_watch(url: str) -> dict:
    """Manual pin from the UI. Parses the URL, fetches PR metadata
    best-effort, writes a row in `review_prs` with source='manual' (or
    bumps to 'both' if GitHub already flagged the same PR)."""
    parsed = _parse_pr_url(url)
    if not parsed:
        raise ValueError(f"Not a GitHub PR URL: {url}")
    repo, number = parsed
    canonical = f"https://github.com/{repo}/pull/{number}"
    # Best-effort: pull title + CI / review / diff in one gh call so
    # the card renders with real data immediately, not placeholders.
    meta = app_state.gh_run_json(
        ["gh", "pr", "view", str(number), "--repo", repo,
         "--json", _REVIEW_ENRICH_FIELDS],
        repo=repo, timeout=15, default={},
    ) or {}
    if not isinstance(meta, dict):
        meta = {}
    author = ""
    if isinstance(meta.get("author"), dict):
        author = meta["author"].get("login", "")
    # Preserve the existing source if this URL is already a github
    # review-request hit -> upgrade to 'both'.
    existing = app_state._db.get_review_pr(canonical)
    source = "both" if existing and existing.get("source") in ("github", "both") else "manual"
    return app_state._db.upsert_review_pr(
        url=canonical,
        repo=repo,
        number=number,
        title=(meta.get("title") or "").strip(),
        author=author,
        status=(meta.get("state") or "open").lower(),
        last_updated=meta.get("updatedAt") or "",
        ci_status=_pr.aggregate_ci_status(meta.get("statusCheckRollup") or []),
        review_status=(meta.get("reviewDecision") or "").lower(),
        my_review_state=_compute_my_review_state(meta, _my_gh_logins()),
        comment_count=len(meta.get("comments") or []) + len(meta.get("reviews") or []),
        additions=meta.get("additions", 0) or 0,
        deletions=meta.get("deletions", 0) or 0,
        head_branch=meta.get("headRefName") or "",
        base_branch=meta.get("baseRefName") or "",
        source=source,
    )


def remove_review_watch(url: str) -> bool:
    """Return True when a row was actually removed."""
    parsed = _parse_pr_url(url)
    if parsed:
        canonical = f"https://github.com/{parsed[0]}/pull/{parsed[1]}"
        if app_state._db.delete_review_watch(canonical):
            return True
    # Fall back to the raw URL as the caller typed it.
    return app_state._db.delete_review_watch(url)


def add_pr(project_id, task_id, number, url, status="open", title="",
           session=None, working_dir="~"):
    """Add PR to task. Returns True on success, raises on duplicate/missing task.

    Raises ValueError if title is empty -- every PR must have a title for display.
    If you only have a number/url, fetch the title from GitHub first.

    Side-effect: after adding the PR, auto-applies `suggest_task_status`
    to promote the task (`not_started` -> `in_review` when a PR appears,
    `in_review` -> `done` when a PR merges). Keeps the state machine
    tight at the write boundary instead of drifting until the user runs
    `check-status` manually.
    """
    if not title or not title.strip():
        raise ValueError("PR title is required. Fetch it from GitHub if not available.")
    # Reject malformed URLs at the write boundary -- a bare repo name
    # (e.g. "monorepo") slipping into the prs.url column historically
    # broke the frontend's PR-detail render because the gh CLI needs
    # `owner/repo`. Match the canonical github.com/<owner>/<repo>/pull/<n>
    # shape; allow stripped trailing slashes and query strings.
    import re as _re
    if not url or not _re.match(
        r"^https?://github\.com/[^/]+/[^/]+/pull/\d+",
        url.strip(),
    ):
        raise ValueError(
            f"PR url must be of the form "
            f"`https://github.com/<owner>/<repo>/pull/<n>`; got {url!r}. "
            f"Pass the full PR URL (e.g. derived from the PR card)."
        )
    task = app_state._db.get_task(project_id, task_id)
    if not task:
        return None  # task not found
    app_state._db.add_pr(project_id, task_id, number=number, url=url,
                              status=status, title=title.strip(),
                              session=session, working_dir=working_dir)
    from .tasks import _append_auto_history
    _append_auto_history(project_id, task_id, f"linked PR #{number}")
    _auto_promote_task_status(project_id, task_id)
    return True


def _auto_promote_task_status(project_id: str, task_id: str) -> None:
    """Run `suggest_task_status` against the current task + PR rows and
    apply the suggestion if one comes back. No-op when the task is
    already in the suggested state.

    Regression context: prod had `not_started` tasks with linked PRs
    and a `closed`-state task with a merged PR, because PR writes
    didn't fan out to the task status. This enforces the state machine
    at every PR mutation site.
    """
    from .tasks import suggest_task_status, _record_status_transition
    fresh = app_state._db.get_task(project_id, task_id)
    if not fresh:
        return
    proj = app_state._db.get_project(project_id) or {}
    has_tickets = bool(proj.get("has_tickets", True))
    suggested = suggest_task_status(fresh, has_tickets=has_tickets)
    old_status = fresh.get("status") or ""
    if not suggested or suggested == old_status:
        return
    # Write the change through the DB layer directly -- going via
    # tasks.update_task would recurse via save_task / emit_event
    # chains that are already noisy on PR events.
    app_state._db.update_task(project_id, task_id, status=suggested)
    _record_status_transition(project_id, task_id, old_status, suggested)
    from .tasks import _emit_task_event
    _emit_task_event("task.status_auto_promoted", project_id, task_id,
                     title=f"Task {task_id}: {old_status} -> {suggested}",
                     message="auto-promoted after PR write",
                     persist=False)


def remove_pr(project_id, task_id, pr_number):
    """Remove PR from task. Returns True if deleted, None if task not found, False if PR not found."""
    task = app_state._db.get_task(project_id, task_id)
    if not task:
        return None
    return app_state._db.delete_pr(project_id, task_id, pr_number)


def _fetch_fork_ci_for_pr(repo, pr_data):
    """Replace statusCheckRollup with fork CI data if applicable.

    Mutates pr_data in place and returns it.
    """
    if _FORK_CI_REPOS.get(repo) and (pr_data.get("state") or "").upper() == "OPEN":
        branch = pr_data.get("headRefName", "")
        if branch:
            fork_jobs = _pr.fetch_fork_ci(branch, app_state.gh_run)
            if fork_jobs:
                pr_data["statusCheckRollup"] = [
                    {"name": j.get("name", ""),
                     "conclusion": (j.get("conclusion") or "").upper(),
                     "status": (j.get("status") or "").upper()}
                    for j in fork_jobs
                ]
    return pr_data


def _fetch_inline_comments(repo, number):
    """Fetch inline review comments via REST API. Returns list."""
    rc = app_state.gh_run(
        ["gh", "api", f"repos/{repo}/pulls/{number}/comments",
         "--paginate", "--jq",
         '[.[] | {user: .user.login, avatar: .user.avatar_url, path: .path, '
         'line: .original_line, side: .side, body: .body, createdAt: .created_at, '
         'diffHunk: .diff_hunk, inReplyToId: .in_reply_to_id, id: .id}]'],
        repo=repo)
    if rc.returncode == 0:
        raw = rc.stdout.strip()
        inline = []
        for chunk in raw.split("\n"):
            chunk = chunk.strip()
            if chunk:
                try:
                    inline.extend(_json.loads(chunk))
                except (ValueError, _json.JSONDecodeError):
                    pass
        return inline
    return []


def _annotate_thread_status(repo, number, inline_comments):
    """Annotate inline comments with review thread resolve/outdated status.

    Fetches thread data via GraphQL and mutates inline_comments in place.
    Returns the annotated list.
    """
    parts = repo.split("/")
    if len(parts) != 2:
        return inline_comments

    gql = (
        'query { repository(owner: "%s", name: "%s") {'
        ' pullRequest(number: %d) { reviewThreads(first: 100) { nodes {'
        ' id isResolved isOutdated comments(first: 1) { nodes { databaseId } }'
        ' } } } } }' % (parts[0], parts[1], number)
    )
    threads = app_state.gh_run_json(
        ["gh", "api", "graphql", "-f", "query=" + gql],
        repo=repo)
    if threads is not None:
        try:
            thread_nodes = threads["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
            # Build map: databaseId -> {threadId, isResolved, isOutdated}
            thread_map = {}
            for tn in thread_nodes:
                first_comment = (tn.get("comments", {}).get("nodes") or [{}])[0]
                db_id = first_comment.get("databaseId")
                if db_id:
                    thread_map[db_id] = {
                        "threadId": tn["id"],
                        "isResolved": tn.get("isResolved", False),
                        "isOutdated": tn.get("isOutdated", False),
                    }
            # Annotate inline comments with thread info
            for ic_item in inline_comments:
                info = thread_map.get(ic_item.get("id"))
                if info:
                    ic_item["threadId"] = info["threadId"]
                    ic_item["isResolved"] = info["isResolved"]
                    ic_item["isOutdated"] = info["isOutdated"]
        except (KeyError, ValueError, _json.JSONDecodeError):
            pass

    return inline_comments


def get_pr_detail(repo, number):
    """Fetch full PR detail from GitHub. Returns dict or None on failure.

    Orchestrates four steps:
    1. Fetch main PR data via ``gh pr view``
    2. Replace CI checks with fork CI if applicable
    3. Fetch and annotate inline review comments
    4. Fetch general (issue) comments
    """
    _DETAIL_FIELDS = (
        "number,title,body,state,author,createdAt,updatedAt,"
        "mergedAt,closedAt,labels,"
        "reviewDecision,reviews,comments,files,additions,deletions,"
        "url,headRefName,baseRefName,mergeable,statusCheckRollup"
    )
    try:
        # 1. Fetch main PR data
        pr_data = app_state.gh_run_json(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", _DETAIL_FIELDS],
            repo=repo)
        if pr_data is None:
            return None

        # 2. Fork CI
        _fetch_fork_ci_for_pr(repo, pr_data)

        # 3. Inline comments + thread status
        pr_data["inlineComments"] = _fetch_inline_comments(repo, number)
        _annotate_thread_status(repo, number, pr_data["inlineComments"])

        # 4. General (issue) comments
        ic = app_state.gh_run(
            ["gh", "api", f"repos/{repo}/issues/{number}/comments",
             "--paginate", "--jq",
             '[.[] | {id: .id, author: {login: .user.login}, body: .body, createdAt: .created_at}]'],
            repo=repo)
        if ic.returncode == 0:
            raw_ic = ic.stdout.strip()
            general = []
            for chunk in raw_ic.split("\n"):
                chunk = chunk.strip()
                if chunk:
                    try:
                        general.extend(_json.loads(chunk))
                    except (ValueError, _json.JSONDecodeError):
                        pass
            if general:
                pr_data["comments"] = general

        return pr_data
    except Exception:
        return None


def get_pr_info(url):
    """Fetch PR review info (cached). Returns dict."""
    cached = _pr_info_cache.get(url)
    if cached and _time.time() - cached["_ts"] < 300:
        return cached

    parts = url.rstrip("/").split("/")
    try:
        number = parts[-1]
        repo = parts[-3]
        org = parts[-4]
    except (IndexError, ValueError):
        return {"error": "invalid PR URL"}

    info = {"url": url, "reviewers": [], "lastComment": None,
            "lastCommentBy": None, "updatedAt": None}

    try:
        d = app_state.gh_run_json(
            ["gh", "api", f"repos/{org}/{repo}/pulls/{number}",
             "--jq", "{requested_reviewers: [.requested_reviewers[].login], updated_at: .updated_at}"],
            repo=f"{org}/{repo}")
        if d is not None:
            info["reviewers"] = d.get("requested_reviewers", [])
            info["updatedAt"] = d.get("updated_at")
    except Exception:
        pass

    try:
        reviews = app_state.gh_run_json(
            ["gh", "api", f"repos/{org}/{repo}/pulls/{number}/reviews",
             "--jq", "[.[] | {user: .user.login, state: .state, submitted: .submitted_at}]"
                     " | sort_by(.submitted) | reverse | .[0:3]"],
            repo=f"{org}/{repo}")
        if reviews:
            for rv in reviews:
                if rv["user"] not in info["reviewers"]:
                    info["reviewers"].append(rv["user"])
            info["lastReviewState"] = reviews[0].get("state")
    except Exception:
        pass

    try:
        d = app_state.gh_run_json(
            ["gh", "api", f"repos/{org}/{repo}/issues/{number}/comments",
             "--jq", "if length > 0 then {user: .[-1].user.login, created: .[-1].created_at} else {} end"],
            repo=f"{org}/{repo}")
        if d is not None and d.get("user"):
            info["lastCommentBy"] = d["user"]
            info["lastComment"] = d["created"]
    except Exception:
        pass

    info["_ts"] = _time.time()
    if len(_pr_info_cache) > 200:
        oldest = sorted(_pr_info_cache, key=lambda k: _pr_info_cache[k].get("_ts", 0))
        for k in oldest[:50]:
            del _pr_info_cache[k]
    _pr_info_cache[url] = info
    return info


_SYNC_VIEW_FIELDS = (
    "number,title,state,url,updatedAt,mergedAt,closedAt,"
    "additions,deletions,comments,reviews,headRefName,baseRefName,author,"
    "statusCheckRollup,reviewDecision"
)


def _status_change_at(item: dict) -> str:
    """Pull the authoritative "when did this PR enter its terminal
    state" timestamp from a `gh pr view` payload. Prefers mergedAt,
    falls back to closedAt for non-merged closes. Returns '' when the
    PR is still open (no transition yet) or the field is missing."""
    return (item.get("mergedAt") or item.get("closedAt") or "") or ""


# Repos that need fork CI (OSS repos where CI runs on personal forks)
_FORK_CI_REPOS = {v: k for k, v in app_state.FORK_TO_UPSTREAM.items() if v in app_state.ALLOWED_REPOS}
_DEFAULT_FORK_REPO = next(iter(_FORK_CI_REPOS.values()), None)


def _fetch_fork_ci(branch, fork_repo=None):
    return _pr.fetch_fork_ci(branch, app_state.gh_run, fork_repo or _DEFAULT_FORK_REPO)


def _aggregate_ci_status(checks):
    return _pr.aggregate_ci_status(checks)


def _is_externally_merged(repo, pr_number):
    """Thin wrapper: passes the gh_run shim to the pr_sync helper so
    tests that monkeypatch `app_state.gh_run` see the override."""
    return _pr.is_externally_merged(repo, pr_number, app_state.gh_run)


def _resolve_pr_status(gh_state, repo, pr_number):
    return _pr.resolve_pr_status(gh_state, repo, pr_number, app_state.gh_run)


def _match_pr_to_task(title):
    return _pr.match_pr_to_task(title, app_state._db)


def map_gh_pr_to_updates(item: dict, repo: str, pr_number: int,
                         existing: dict | None = None) -> dict:
    """Pure mapper: `gh pr view` response -> kwargs for `update_pr_by_number`.

    Shared by three paths:
      * `_update_pr_from_gh` (scheduled sync / dirty refresh in CLI path)
      * `routes/prs._fetch_pr_detail` (async poller in `/api/all-prs/sync-stream`)
      * `routes/prs.refresh_single_pr` (manual `/api/pr-refresh/{number}`)

    Returns the full field dict every writer persists. `existing` lets
    callers pass the current DB row so we can avoid clobbering a
    `status_changed_at` that was already backfilled from an earlier
    mergedAt/closedAt value.
    """
    gh_state = (item.get("state") or "").upper()
    ci_checks = item.get("statusCheckRollup") or []
    if _FORK_CI_REPOS.get(repo) and gh_state == "OPEN":
        fork_ci = _fetch_fork_ci(item.get("headRefName", ""))
        if fork_ci:
            ci_checks = fork_ci
    updates = {
        "status": _resolve_pr_status(gh_state, repo, pr_number),
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "ci_status": _aggregate_ci_status(ci_checks),
        "review_status": (item.get("reviewDecision") or "").lower(),
        "comment_count": len(item.get("comments") or []) + len(item.get("reviews") or []),
        "additions": item.get("additions", 0) or 0,
        "deletions": item.get("deletions", 0) or 0,
        "author": (item.get("author") or {}).get("login", ""),
        "head_branch": item.get("headRefName", ""),
        "base_branch": item.get("baseRefName", ""),
        "last_updated": item.get("updatedAt", ""),
    }
    # `status_changed_at` is a one-way backfill: once we have a real
    # GitHub-authoritative transition timestamp (mergedAt / closedAt),
    # never overwrite it from a later poll (the transition time is
    # immutable, Eva shouldn't drift it forward with fresh observations).
    changed_at = _status_change_at(item)
    if changed_at and not ((existing or {}).get("status_changed_at") or "").strip():
        updates["status_changed_at"] = changed_at
    return updates


def _update_pr_from_gh(pr_number, item, repo):
    """Update a PR in the DB from a gh API response dict. Also auto-
    promotes the owning task's status if the PR state change triggers
    it (e.g. 'open' -> 'merged' should bump task to 'done')."""
    existing = app_state._db.find_pr_by_number(pr_number)
    prev_status = (existing or {}).get("status") or ""
    updates = map_gh_pr_to_updates(item, repo, pr_number, existing)
    app_state._db.update_pr_by_number(pr_number, **updates)
    # Fetch the post-update row to find the owning task, then run the
    # state-machine promoter. Skipped for PRs with no task link (bare
    # rows inserted via admin path).
    row = existing or app_state._db.find_pr_by_number(pr_number) or {}
    project = row.get("project") or ""
    task_id = row.get("task_id") or ""
    if project and task_id:
        # Record the PR-side transition before task promotion so the
        # history reads in causal order: PR merged -> task done.
        new_status = updates.get("status") or ""
        if prev_status != "merged" and new_status == "merged":
            from .tasks import _append_auto_history
            _append_auto_history(project, task_id, f"PR #{pr_number} merged")
        # Auto-fire the task's `sync` action whenever a PR transitions
        # to a terminal status (open -> merged, or open -> closed
        # without merge). Equivalent to clicking the Sync Status
        # button in the TaskCard UI: spawns / resumes the task's
        # agent session and pastes the sync prompt. Skip when the
        # task itself is already terminal -- there's nothing left
        # for sync to reconcile.
        from eva_db import TERMINAL_TASK_STATUSES as _TERMINAL
        terminal_pr_transition = (
            (prev_status != "merged" and new_status == "merged")
            or (prev_status not in ("merged", "closed") and new_status == "closed")
        )
        if terminal_pr_transition:
            task_row = app_state._db.get_task(project, task_id)
            task_status = (task_row or {}).get("status") or ""
            if task_status not in _TERMINAL:
                from .sessions import fire_action
                fire_action(
                    project, task_id, action_id="sync",
                    reason=f"PR #{pr_number} {new_status}",
                )
        _auto_promote_task_status(project, task_id)


def build_search_args(repo_or_owner):
    """Turn a `repo_or_owner` token into `(search_arg, gh_repo_hint)`.

    `"owner:org"` searches the whole org (the repo hint just picks a
    token-bearing repo in that org for gh-account selection). Everything
    else is treated as a fully-qualified `org/repo`.

    Shared by the sync CLI path (`sync_prs_generator`) and the async
    stream path (`routes/prs.sync_all_prs_stream`) so both interpret
    `_build_repo_authors`'s key format identically.
    """
    if repo_or_owner.startswith("owner:"):
        org = repo_or_owner[len("owner:"):]
        return ["--owner", org], org + "/main"
    return ["--repo", repo_or_owner], repo_or_owner


def build_pr_search_cmd(search_arg, author, state, limit):
    """`gh search prs` command factory shared across discover paths."""
    return ["gh", "search", "prs"] + search_arg + [
        "--author", author, "--state", state, "--limit", limit,
        "--json", "number,title,url,state,repository",
    ]


def ingest_discovered_item(item, repo_or_owner, state):
    """Given one `gh search prs` result, persist it when it's new and
    matches a task. Returns `{"number", "url", "repo", "match"}` on
    insert, or None.

    Factored out of the sync + async discover loops so both stay in
    lockstep on field extraction, fork-upstream resolution, allow-list
    filtering, task matching, and quick_status computation.
    """
    pr_number = item.get("number")
    if not pr_number:
        return None
    if app_state._db.find_pr_by_number(pr_number):
        return None
    # For `--owner` searches the hit carries a `repository` object; use
    # its `nameWithOwner` as the authoritative repo. Otherwise fall back
    # to the caller's search token.
    item_repo = repo_or_owner
    repo_info = item.get("repository", {})
    if isinstance(repo_info, dict) and repo_info.get("nameWithOwner"):
        item_repo = repo_info["nameWithOwner"]
    elif isinstance(repo_info, str):
        item_repo = repo_info
    actual_repo = app_state.FORK_TO_UPSTREAM.get(item_repo, item_repo)
    if not app_state.is_repo_allowed(actual_repo):
        return None
    match = _match_pr_to_task(item.get("title", ""))
    if not match:
        return None
    url = "https://github.com/" + actual_repo + "/pull/" + str(pr_number)
    gh_st = (item.get("state") or state).upper()
    quick_status = _resolve_pr_status(gh_st, actual_repo, pr_number)
    app_state._db.add_pr(
        project=match[0], task_id=match[1],
        number=pr_number, url=url,
        title=item.get("title", ""), status=quick_status,
    )
    return {"number": pr_number, "url": url, "repo": actual_repo, "match": match}


def _refresh_pr_from_gh(pr_number, pr_url):
    """Fetch latest from `gh pr view` for one PR and persist via _update_pr_from_gh.

    Returns True when the DB was updated, False when the repo isn't allowed,
    the gh call failed, or the URL couldn't be parsed. Used by sync_prs_generator's
    dirty / full refresh loops so both paths share identical semantics.
    """
    repo = repo_from_pr_url(pr_url)
    if not repo or not app_state.is_repo_allowed(repo):
        return False
    item = app_state.gh_run_json(
        ["gh", "pr", "view", str(pr_number), "--repo", repo,
         "--json", _SYNC_VIEW_FIELDS],
        repo=repo, timeout=15,
    )
    if item is None:
        return False
    _update_pr_from_gh(pr_number, item, repo)
    return True


def sync_prs_generator(full=False):
    """Generator that yields progress dicts during PR sync.

    Synchronous version for CLI use. Yields dicts with 'phase' key:
      {phase: 'start'}, {phase: 'dirty', count: N},
      {phase: 'dirty_update', current: N, total: N},
      {phase: 'discover', discovered: N},
      {phase: 'update', current: N, total: N, updated: N},
      {phase: 'done', discovered: N, updated: N, total: N}
    """
    yield {"phase": "start", "full": bool(full)}
    repo_authors = app_state._build_repo_authors()
    updated = 0

    # Phase 1: dirty PRs
    dirty_prs = app_state._db.list_dirty_prs()
    yield {"phase": "dirty", "count": len(dirty_prs)}

    for i, pr in enumerate(dirty_prs):
        pr_number = pr["number"]
        pr_url = pr.get("url", "")
        try:
            if _refresh_pr_from_gh(pr_number, pr_url):
                app_state._db.clear_pr_dirty(pr_number)
                updated += 1
        except Exception:
            pass
        yield {"phase": "dirty_update", "current": i + 1, "total": len(dirty_prs)}

    # Phase 2: discover new PRs
    discovered = 0
    newly_discovered = []

    limit = "200" if full else "30"
    for repo_or_owner, author in repo_authors.items():
        search_arg, gh_repo_hint = build_search_args(repo_or_owner)
        for state in ["open", "closed"]:
            try:
                items = app_state.gh_run_json(
                    build_pr_search_cmd(search_arg, author, state, limit),
                    repo=gh_repo_hint, timeout=20,
                )
                if items is None:
                    continue
                for item in items:
                    added = ingest_discovered_item(item, repo_or_owner, state)
                    if added:
                        newly_discovered.append(
                            {"number": added["number"], "url": added["url"]}
                        )
                        discovered += 1
            except Exception:
                pass

    yield {"phase": "discover", "discovered": discovered}

    # Phase 3: update details
    detail_prs = app_state._db.list_all_prs() if full else newly_discovered
    total = len(detail_prs)

    for i, pr in enumerate(detail_prs):
        pr_number = pr.get("number")
        pr_url = pr.get("url", "")
        if not pr_number or not pr_url:
            continue
        try:
            if _refresh_pr_from_gh(pr_number, pr_url):
                updated += 1
        except Exception:
            pass
        if (i + 1) % 6 == 0 or i + 1 == total:
            yield {"phase": "update", "current": i + 1, "total": total, "updated": updated}

    yield {"phase": "done", "discovered": discovered, "updated": updated, "total": total}
