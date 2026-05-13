"""PR synchronization helpers: CI aggregation, merge detection, task matching."""

import json as _json
import re as _re


# Ticket-prefix -> JIRA URL mappings live in the settings DB (see
# `common.settings.KEY_JIRA_TICKET_URL_PREFIXES`). The dict below is
# the empty fallback for unit tests that import pr_sync without
# booting the server; production code paths read through the
# accessor helper.
TICKET_URL_PREFIXES: dict[str, str] = {}


def _settings_ticket_url_prefixes() -> dict[str, str]:
    try:
        from common import settings as _settings
        return _settings.get_ticket_url_prefixes() or TICKET_URL_PREFIXES
    except Exception:
        return TICKET_URL_PREFIXES


def _check_name(check) -> str:
    """Pull the human-readable check name from any GitHub check shape."""
    return check.get("context") or check.get("name") or ""


def _is_blocking_check(check) -> bool:
    """Convention: check names prefixed with `[Non-Blocking]` don't gate
    merge. Eva treats them as informational only -- a failing non-blocking
    check shouldn't make the PR look red. Default to blocking for any
    name that doesn't carry the prefix."""
    return not _check_name(check).lower().startswith("[non-blocking]")


# States from any of the three GitHub check shapes (CheckRun conclusion,
# CheckRun status, StatusContext state), case-insensitive.
_FAILURE_STATES = frozenset({
    "failure", "error",
    # Cancelled / timed-out / stale / startup-failure are all terminal
    # broken states -- not pending, not success. They must be classified
    # BEFORE the pending check so `[CANCELLED, PENDING]` reports failure
    # rather than hiding the cancellation behind an in-progress job.
    "cancelled", "timed_out", "action_required", "stale", "startup_failure",
})
_PENDING_STATES = frozenset({
    "pending", "in_progress", "queued", "expected",
})
_SUCCESS_STATES = frozenset({
    "success", "neutral", "skipped",
})


def aggregate_ci_status(checks) -> str:
    """Aggregate CI check statuses into a single status string.

    Priority: failure > pending > success > unknown.

    GitHub mixes two check shapes in `statusCheckRollup`:
      - `__typename: CheckRun` -> `conclusion` (when finished) / `status`
        (lifecycle: QUEUED/IN_PROGRESS/COMPLETED).
      - `__typename: StatusContext` -> `state` only
        (PENDING/SUCCESS/FAILURE/ERROR). Used by older status-check API.

    Non-blocking checks ([Non-Blocking] prefix in the name) are dropped
    from the aggregation so a failing non-blocking job doesn't paint the
    PR red. If a PR happens to have only non-blocking checks we fall back
    to the full set rather than reporting "unknown".
    """
    if not checks:
        return "unknown"

    blocking = [c for c in checks if _is_blocking_check(c)]
    eval_set = blocking or checks

    states = [
        (c.get("conclusion") or c.get("status") or c.get("state") or "").lower()
        for c in eval_set
    ]
    if any(s in _FAILURE_STATES for s in states):
        return "failure"
    if any(s in _PENDING_STATES for s in states):
        return "pending"
    if states and all(s in _SUCCESS_STATES for s in states):
        return "success"
    return "unknown"


def _settings_external_merge_repos() -> set[str]:
    """Repos that close-PRs externally (committer cherry-pick + close)
    instead of using the GitHub merge button. For these, a `CLOSED`
    PR may actually be merged via referenced commit -- we have to
    inspect issue events to disambiguate.

    Reads `service.github.external_merge_repos` (list of `org/repo`
    strings). Empty by default -- a fresh OSS install treats every
    `CLOSED` PR as `closed`, which is correct for repos that use
    GitHub's merge button. Acme Widgets is the canonical example
    that needs to opt in via the Settings UI.
    """
    try:
        from common import settings as _settings
        v = _settings.get_value(
            "service.github.external_merge_repos", default=None)
        if isinstance(v, list):
            return {str(x) for x in v if isinstance(x, str)}
    except Exception:
        pass
    return set()


def is_externally_merged(repo: str, pr_number: int, gh_run_fn) -> bool:
    """Check if a CLOSED PR was actually merged via external workflow.

    Some upstream repos (e.g. Acme Widgets) use external merge: a
    committer cherry-picks the change and closes the PR, so GitHub
    never sets `merged=true`. For those repos we look at the issue
    events stream for a `referenced` or `closed` event whose
    `commit_id` is non-null -- that's the merge commit.

    `repo` is the full `org/repo` so the gh API call hits the right
    upstream namespace (forks have their own events stream).
    """
    if not repo or "/" not in repo:
        return False
    try:
        r = gh_run_fn(
            ["gh", "api", f"repos/{repo}/issues/{pr_number}/events",
             "--jq", '[.[] | select(.event=="referenced" or .event=="closed")'
                     ' | .commit_id] | map(select(. != null)) | length'],
            repo=repo, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip()) > 0
    except Exception:
        pass
    return False


def resolve_pr_status(gh_state: str, repo: str, pr_number: int, gh_run_fn) -> str:
    """Resolve a PR's status string from the gh CLI state.

    For repos opted into external-merge handling (see
    `service.github.external_merge_repos`), a `CLOSED` PR is
    re-checked via issue events to detect a referenced merge commit.
    Otherwise `CLOSED` maps directly to `closed`.
    """
    if gh_state == "MERGED":
        return "merged"
    if gh_state == "CLOSED":
        if repo in _settings_external_merge_repos():
            if is_externally_merged(repo, pr_number, gh_run_fn):
                return "merged"
        return "closed"
    return "open"


def fetch_fork_ci(branch, gh_run_fn, fork_repo=""):
    """Fetch real CI jobs from fork repo for an upstream-OSS PR branch.

    `fork_repo` (e.g. "alice/widgets") MUST be provided -- callers
    derive it from the user-configured FORK_TO_UPSTREAM map; an
    empty value short-circuits to None so we don't silently shell
    out to a wrong repo.

    Returns list of job dicts compatible with statusCheckRollup
    format, or None on missing repo / non-zero gh exit.
    """
    if not fork_repo:
        return None
    try:
        result = gh_run_fn(
            ["gh", "run", "list", "--repo", fork_repo, "--branch", branch,
             "--workflow", "Build", "--limit", "1",
             "--json", "databaseId,status,conclusion"],
            repo=fork_repo, timeout=10,
        )
        if result.returncode != 0:
            return None
        runs = _json.loads(result.stdout)
        if not runs:
            return None
        run_id = runs[0]["databaseId"]

        result = gh_run_fn(
            ["gh", "run", "view", str(run_id), "--repo", fork_repo,
             "--json", "jobs", "--jq", '.jobs[] | {name, conclusion, status}'],
            repo=fork_repo, timeout=10,
        )
        if result.returncode != 0:
            return None
        jobs = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                try:
                    jobs.append(_json.loads(line))
                except (ValueError, _json.JSONDecodeError):
                    pass
        return jobs if jobs else None
    except Exception:
        return None


def extract_ticket(title: str):
    """Extract JIRA ticket ID from PR title. Returns str or None.

    E.g. '[EX-56253][PYTHON] Fix ...' -> 'EX-56253'
    """
    m = _re.search(r'\[([A-Z]+-\d+)\]', title)
    return m.group(1) if m else None


def strip_title_tags(title: str) -> str:
    """Strip leading [TAG] prefixes from a PR title.

    E.g. '[EX-56253][PYTHON][DOCS] Fix ...' -> 'Fix ...'
    """
    return _re.sub(r'^(\[.*?\]\s*)+', '', title).strip()


def ticket_url(ticket_id: str) -> str:
    """Build JIRA URL from ticket ID using the configured prefix map.

    Returns "" when no configured prefix matches (or when no prefixes
    are configured at all -- a fresh install has none until the user
    seeds them via yaml or the Settings UI).
    """
    for prefix, base_url in _settings_ticket_url_prefixes().items():
        if ticket_id.startswith(prefix):
            return base_url + ticket_id
    return ""


def match_pr_to_task(title, db):
    """Match a PR to an existing task by JIRA ticket in title.

    Returns (project, task_id) or None. PRs whose ticket has no
    matching task are silently skipped -- task creation is a manual
    user action via the UI / `eva-cli`.
    """
    ticket_id = extract_ticket(title)
    if not ticket_id:
        return None
    return db.find_task_by_ticket(ticket_id)
