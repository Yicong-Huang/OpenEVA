"""Multi-instance JIRA -> tickets-cache layer.

Each user-configured JIRA instance is stored as a dict in the
`service.jira.instances` settings list. The Tickets page calls
`sync_all()` (one round trip per instance) and `list_tickets()`
(merged across instances). Every cached row carries
`instance_name` so cross-instance key collisions can't happen.

Schema for an instance entry:

    {
      "name": "primary",                    # short stable id, used in URLs
      "base_url": "https://issues.example.org/jira",
      "auth_type": "bearer",                # 'basic' | 'bearer'
      "email": "alice@example.com",         # only used for 'basic'
      "api_token": "...",                   # secret; not exposed via list API
      "jql": "assignee = currentUser() ..."
    }
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import app_state
from adapters import jira as _jira
from . import settings as _settings


def _now_iso() -> str:
    # Microsecond precision so two syncs within the same second still
    # produce monotonically increasing `synced_at` values.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")


# ---- Instance config ----

def _normalise_instance(raw: Any) -> dict | None:
    """Coerce a raw settings entry into a usable instance dict, or
    return None when required fields are missing. Names get
    fallbacks so the UI never blows up on a half-typed row."""
    if not isinstance(raw, dict):
        return None
    name = (raw.get("name") or "").strip()
    base_url = (raw.get("base_url") or "").strip()
    api_token = raw.get("api_token") or ""
    if not (base_url and api_token):
        return None
    auth_type = (raw.get("auth_type") or _jira.AUTH_BASIC).strip().lower()
    if auth_type not in _jira.VALID_AUTH_TYPES:
        auth_type = _jira.AUTH_BASIC
    return {
        "name": name or base_url,  # fallback so primary key stays unique
        "base_url": base_url,
        "auth_type": auth_type,
        "email": raw.get("email") or "",
        "api_token": api_token,
        "jql": (raw.get("jql") or "").strip() or _settings.DEFAULT_JIRA_JQL,
    }


def list_instances() -> list[dict]:
    """Return all configured instances."""
    raw = _settings.get_value(_settings.KEY_JIRA_INSTANCES) or []
    out = []
    seen = set()
    for r in raw:
        n = _normalise_instance(r)
        if not n:
            continue
        if n["name"] in seen:
            continue  # de-dup names defensively
        seen.add(n["name"])
        out.append(n)
    return out


def is_configured() -> bool:
    """True iff at least one valid instance exists."""
    return len(list_instances()) > 0


def public_instances() -> list[dict]:
    """Same as `list_instances` but with `api_token` redacted -- safe
    to return through the HTTP layer."""
    out = []
    for inst in list_instances():
        out.append({
            "name": inst["name"],
            "base_url": inst["base_url"],
            "auth_type": inst["auth_type"],
            "email": inst["email"],
            "jql": inst["jql"],
            "has_token": bool(inst["api_token"]),
        })
    return out


# ---- Sync ----

def _normalise_issue(issue: dict, instance: dict) -> dict:
    """Project a raw JIRA REST issue dict onto our cache columns.

    Defensive: every nested .get("foo", {}).get("bar") so an
    unexpected payload shape never raises -- we just store an empty
    string and let the user notice via the UI."""
    fields = issue.get("fields") or {}
    assignee = (fields.get("assignee") or {})
    reporter = (fields.get("reporter") or {})
    project = (fields.get("project") or {})
    status = (fields.get("status") or {})
    priority = (fields.get("priority") or {})
    issue_type = (fields.get("issuetype") or {})
    # Server (v2) returns plain text in `description`; Cloud (v3)
    # returns ADF. Strings round-trip cleanly; non-strings get
    # str()-ified so the cache column never blows up.
    description = fields.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    # JIRA Server returns `name` for assignee; Cloud returns
    # `emailAddress` + `displayName`. Pick whichever's present so the
    # assignee filter works in both cases.
    assignee_email = (
        assignee.get("emailAddress")
        or assignee.get("name")
        or ""
    )
    reporter_email = (
        reporter.get("emailAddress")
        or reporter.get("name")
        or ""
    )
    # Phase-1 enrichment fields. Lists land as JSON-encoded strings so
    # the cache schema stays pyr; the route layer parses them back
    # for the UI. Defensive: every shape that could be missing/null on
    # one server vs the other gets a sane default.
    import json as _json
    labels = fields.get("labels") or []
    components = [
        c.get("name") for c in (fields.get("components") or [])
        if isinstance(c, dict) and c.get("name")
    ]
    fix_versions = [
        v.get("name") for v in (fields.get("fixVersions") or [])
        if isinstance(v, dict) and v.get("name")
    ]
    parent = (fields.get("parent") or {})
    parent_key = parent.get("key", "") or ""
    resolution = (fields.get("resolution") or {})
    resolution_name = resolution.get("name", "") if isinstance(resolution, dict) else ""
    # statusCategory.key is one of `new` / `indeterminate` / `done` /
    # `undefined`; this is what JIRA itself uses to colour the badge.
    # Normalised so the UI can render a consistent chip across servers.
    status_category = (status.get("statusCategory") or {}).get("key", "") or ""
    return {
        "instance_name": instance["name"],
        "key": issue.get("key", ""),
        "summary": fields.get("summary", "") or "",
        "description": description,
        "status": status.get("name", "") or "",
        "priority": priority.get("name", "") or "",
        "issue_type": issue_type.get("name", "") or "",
        "project_key": project.get("key", "") or "",
        "assignee_email": assignee_email,
        "reporter_email": reporter_email,
        "url": _jira.issue_url(instance["base_url"], issue.get("key", "")),
        "created_at": fields.get("created", "") or "",
        "updated_at": fields.get("updated", "") or "",
        "labels": _json.dumps(labels) if labels else "",
        "components": _json.dumps(components) if components else "",
        "fix_versions": _json.dumps(fix_versions) if fix_versions else "",
        "parent_key": parent_key,
        "resolution": resolution_name or "",
        "status_category": status_category,
    }


# Fields whose changes are interesting enough to fire `ticket.updated`.
# `synced_at` and `description` are intentionally excluded -- the
# former changes every sync (would emit on every tick), the latter is
# multi-line free-form and changes shape between Cloud (ADF) and
# Server (text) so a `description` diff is too noisy to be useful.
_TICKET_TRACKED_FIELDS = (
    "status", "priority", "issue_type", "assignee_email",
    "reporter_email", "summary", "updated_at", "resolution",
    "status_category", "parent_key",
)


def _ticket_diff(prev: dict | None, new: dict) -> list[str]:
    """Return the names of tracked fields whose value changed between
    a previously-cached row and the freshly-synced one. An empty
    return means "no notable change" -- caller skips emitting."""
    if not prev:
        return []
    changed: list[str] = []
    for field in _TICKET_TRACKED_FIELDS:
        if (prev.get(field) or "") != (new.get(field) or ""):
            changed.append(field)
    return changed


def _emit_ticket_event(kind: str, record: dict, changes: list[str]) -> None:
    """Push a `ticket.created` / `ticket.updated` onto the event bus.

    `kind` is the verb (created/updated). `changes` is the list of
    fields the diff caught (empty for created). The event payload
    deliberately includes only what the UI needs to repaint a card --
    full ticket detail is fetched on click.
    """
    key = record.get("key", "")
    instance = record.get("instance_name", "")
    summary = (record.get("summary") or "")[:120]
    title = f"Ticket {kind}: {key}"
    message = f"{summary} ({', '.join(changes)})" if changes else summary
    try:
        app_state.emit_event(
            f"ticket.{kind}",
            {
                "title": title,
                "message": message,
                "severity": "info",
                "source_id": key,
                "url": record.get("url", ""),
                # Include the changed-fields list so a future UI can
                # highlight just the deltas.
                "changes": changes,
                "ticket_key": key,
                "instance_name": instance,
            },
            # Don't persist these -- the user gets ticket spam in
            # /events otherwise (a JIRA sync routinely touches
            # dozens of tickets). The SSE push is what we care
            # about for live UI repaint.
            persist=False,
        )
    except Exception as e:
        # Event-bus failures must not break sync. The next tick
        # retries the upsert anyway; missing one event is harmless.
        print(f"[tickets-sync] emit {kind} for {key} failed: {e}",
              flush=True)


def sync_one(instance: dict, *, http_get=None) -> dict:
    """Sync a single JIRA instance into the cache.

    For each issue: read the existing cached row, compare tracked
    fields to the freshly-fetched payload, upsert, then emit
    `ticket.created` (when the row is new) or `ticket.updated` (when
    any tracked field changed). Returns
    `{name, count, pruned, created, updated, jql}`.
    """
    started_at = _now_iso()
    issues = _jira.search_issues(
        base_url=instance["base_url"],
        auth_type=instance["auth_type"],
        email=instance["email"],
        api_token=instance["api_token"],
        jql=instance["jql"],
        http_get=http_get,
    )
    count = 0
    created = 0
    updated = 0
    for issue in issues:
        record = _normalise_issue(issue, instance)
        if not record["key"]:
            continue
        record["synced_at"] = started_at
        prev = app_state._db.get_ticket(
            record["key"], instance_name=instance["name"],
        )
        app_state._db.upsert_ticket(**record)
        count += 1
        if prev is None:
            created += 1
            _emit_ticket_event("created", record, [])
        else:
            changes = _ticket_diff(prev, record)
            if changes:
                updated += 1
                _emit_ticket_event("updated", record, changes)
    pruned = app_state._db.delete_tickets_synced_before(
        started_at, instance_name=instance["name"],
    )
    return {
        "name": instance["name"],
        "count": count,
        "created": created,
        "updated": updated,
        "pruned": pruned,
        "jql": instance["jql"],
    }


def sync_all(*, http_get=None) -> dict:
    """Sync every configured instance. Returns
    `{instances: [{name, count, pruned, ...}], total_count, errors: [...]}`.

    Per-instance errors are captured (not raised) so a single broken
    JIRA doesn't kill the whole sync.
    """
    instances = list_instances()
    if not instances:
        raise ValueError(
            "No JIRA instances configured -- add one in Settings."
        )
    summaries: list[dict] = []
    errors: list[dict] = []
    for inst in instances:
        try:
            summaries.append(sync_one(inst, http_get=http_get))
        except Exception as e:
            errors.append({"name": inst["name"], "error": str(e)})
    return {
        "instances": summaries,
        "total_count": sum(s["count"] for s in summaries),
        "errors": errors,
    }


# Back-compat: the old single-JIRA route kept the name `sync()`. Tests
# and existing callers use it. Routes route the same shape.
def sync(*, http_get=None) -> dict:
    """Legacy single-JIRA wrapper around `sync_all`. Returns the
    old-shape `{count, pruned, jql}` for the first instance so old
    callers still work; new callers should prefer `sync_all`."""
    out = sync_all(http_get=http_get)
    if not out["instances"]:
        return {"count": 0, "pruned": 0, "jql": "",
                "errors": out["errors"]}
    first = out["instances"][0]
    return {
        "count": out["total_count"],
        "pruned": sum(s["pruned"] for s in out["instances"]),
        "jql": first["jql"],
        "instances": out["instances"],
        "errors": out["errors"],
    }


# ---- List + view enrichment ----

def _project_prefix(key: str) -> str:
    """Return the JIRA project prefix from a key. `EX-123` -> `EX`,
    `EX-1234` -> `ES`. Empty when the key has no dash, which the UI
    can render as a generic 'misc' chip."""
    if not key or "-" not in key:
        return ""
    return key.rsplit("-", 1)[0]


def enrich_for_view(ticket: dict) -> dict:
    """Hydrate a cached ticket row for the API.

    - Parse JSON-encoded list columns (`labels`, `components`,
      `fix_versions`) so the UI doesn't double-decode.
    - Add `category` (project prefix, e.g. `EX` / `ES` / `INTKEY`)
      so the card can show a coloured chip without parsing on the
      client.
    - Add `linked_tasks` (reverse-lookup via `find_tasks_by_ticket`)
      so the user can jump from a ticket card back to the task that
      tracks it.
    - Add `session_name` so the frontend can index the ticket into the
      global session-status snapshot. The previous per-row
      `session_alive` / `session_status` are no longer attached -- the
      snapshot service is authoritative; stamping them again here just
      created two copies of the same data.
    """
    import json as _json
    out = dict(ticket)
    for list_col in ("labels", "components", "fix_versions"):
        raw = out.get(list_col) or ""
        try:
            parsed = _json.loads(raw) if raw else []
        except (ValueError, TypeError):
            parsed = []
        out[list_col] = parsed if isinstance(parsed, list) else []
    out["category"] = _project_prefix(out.get("key", ""))
    # Reverse-link to any task whose ticket_id == this ticket's key.
    # find_tasks_by_ticket returns rows shaped as (project, task_id,
    # status, ...); we only need the first two but tolerate longer
    # tuples so a future schema change doesn't break the enricher.
    try:
        linked = app_state._db.find_tasks_by_ticket(out.get("key", ""))
    except Exception:
        linked = []
    out["linked_tasks"] = [
        {"project": row[0], "task_id": row[1],
         "status": row[2] if len(row) > 2 else ""}
        for row in (linked or [])
    ]
    out["session_name"] = session_name_for_ticket(
        out.get("key", ""), instance_name=out.get("instance_name", "") or "",
    )
    return out


def list_tickets(limit: int = 100) -> list[dict]:
    """Return cached tickets across every configured instance, each
    enriched for view (parsed list columns + category prefix +
    reverse-linked tasks).

    Tickets aren't filtered by assignee here because each instance has
    its own assignee identity (email vs username). The JQL on the
    instance is what scopes "my tickets" -- we trust it and return
    everything that's currently cached for any configured instance.

    Tickets ALL of whose `linked_tasks` belong to hidden projects
    (per `ui.hidden_projects`) get filtered out. Free-floating
    tickets (no linked tasks at all) are always shown -- they aren't
    tied to any project's visibility.
    """
    if not list_instances():
        return []
    rows = app_state._db.list_tickets(limit=limit)
    enriched = [enrich_for_view(r) for r in rows]
    hidden = _settings.get_hidden_projects()
    if not hidden:
        return enriched
    out = []
    for t in enriched:
        links = t.get("linked_tasks") or []
        if links and all(lk.get("project") in hidden for lk in links):
            continue
        out.append(t)
    return out


def get_one(key: str, *, instance_name: str = "") -> dict | None:
    """Fetch one ticket by `(instance_name, key)` and return its
    view-enriched form. Returns None when the key isn't cached.

    When `instance_name` is empty, walk every configured instance to
    find a match -- mirrors the same fallback logic as `track()`,
    so callers (the triage / detail panels) don't need to know which
    instance owns a key."""
    if instance_name:
        row = app_state._db.get_ticket(key, instance_name=instance_name)
        return enrich_for_view(row) if row else None
    # Walk every configured instance.
    for inst in list_instances():
        row = app_state._db.get_ticket(key, instance_name=inst["name"])
        if row:
            return enrich_for_view(row)
    return None


# Plain heuristics for surfacing file paths inside a free-form ticket
# description. Aimed at flaky-test tickets where the body is usually
# a stack trace + log excerpt. We don't try to be perfect -- only
# matches that look enough like file paths to be useful when the user
# clicks "Triage".
_FILE_PATH_RE = re.compile(
    r"\b("
    r"[a-zA-Z0-9_./-]+\.(?:py|java|go|rs|ts|tsx|js|jsx|"
    r"sql|sh|c|h|hpp|cpp|cc|md|yaml|yml)"
    r")\b"
)
# Bazel-style build-target prefix (e.g. `//widgets/images/extensions/widget`
# from a `//widgets/images/extensions/widget:18.0.x-suite` target).
# Useful for "Failing target detected" CI tickets where the JIRA
# `components` field is empty but the description carries a structured
# target pointer the team owner can be derived from.
#
# Each path segment must be at least 1 word-char and segments are
# separated by single slashes -- this avoids matching mid-URL fragments
# like `https://example.com//foo:bar` where the `//foo` is part of the
# URL scheme + path, not a Bazel target. The `(?<![:/])` lookbehind
# rules out the `://` of a URL scheme too.
_BAZEL_TARGET_RE = re.compile(
    r"(?<![:/])//"
    r"([a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)+)"
    r":[a-zA-Z0-9_.-]+"
)


def _extract_file_paths(text: str, *, limit: int = 10) -> list[str]:
    """Pull file-path-looking tokens out of a ticket description.

    The triage report uses these as "files this ticket likely
    references" -- a starting point for the user (or the git-blame
    integration) to identify the recent commits / authors that might
    have caused the flake.

    Picks up two shapes:
      1. Conventional file paths ending in known extensions
         (`.py`, `.java`, `.ts`, etc.).
      2. Bazel-style build-target directories
         (`//widgets/foo/bar:label` -> `widgets/foo/bar`). Common in
         "Failing target detected" CI tickets that don't have
         explicit file paths in the body.

    Caps at `limit` items and de-duplicates so a stack trace mentioning
    the same file 50 times only emits it once.
    """
    if not text:
        return []
    seen: list[str] = []

    def _add(path: str) -> bool:
        if path not in seen:
            seen.append(path)
        return len(seen) >= limit

    for m in _FILE_PATH_RE.finditer(text):
        path = m.group(1)
        # Drop bare 1-segment matches like "Foo.py" without a directory
        # prefix -- those are usually class names from package paths
        # already captured elsewhere in the trace.
        if "/" not in path and "." in path and len(path) < 40:
            continue
        if _add(path):
            return seen

    # Bazel targets -- only surface the directory (the part before
    # the `:label`) so it can be matched against a local repo's
    # filesystem layout for git-blame.
    for m in _BAZEL_TARGET_RE.finditer(text):
        directory = m.group(1)
        # Skip paths that ARE just a single segment (`foo` -> `foo`);
        # those are too generic and rarely have a meaningful BUILD owner.
        if "/" not in directory:
            continue
        if _add(directory):
            return seen
    return seen


def _git_blame_for_files(file_paths: list[str], *, runner=None) -> list[dict]:
    """Phase-2 of triage: for each file path, find the matching local
    repo (per `service.git.local_repo_paths` setting), run
    `git log -1 --format=...` against the file, and return blame info.

    Returns a list (not a dict) so the UI can show the same file
    matched in two different repos as two rows.

    Each row:
      {
        "file": "src/Foo.py",
        "repo": "acme/widgets",
        "local_path": "/home/alice/code/widgets",
        "author_name": "...",
        "author_email": "...",
        "commit": "abc1234",
        "committed_at": "2026-04-25T08:00:00Z",
        "subject": "first line of commit message",
      }

    Files that aren't found in any configured local repo are silently
    skipped (the UI still has the file path from the description).
    Errors from individual `git log` calls are also swallowed -- one
    bad file shouldn't kill the whole report.

    `runner` is the subprocess-runner injected for tests. Default uses
    `subprocess.run` with a 5s timeout per call.
    """
    if not file_paths:
        return []
    repo_paths = _settings.get_local_repo_paths()
    if not repo_paths:
        return []

    if runner is None:
        import os as _os_env
        import subprocess as _sp
        # Big monorepos can take 5-15s for a `git log
        # -1 -- <path>` because the path-pruning happens after the
        # commit walk. 30s is generous but still bounded so a hung
        # call doesn't block the whole HTTP request thread.
        # GIT_OPTIONAL_LOCKS=0 + --no-replace-objects skip the
        # locking + replace-ref refresh that adds latency.
        env = {**_os_env.environ, "GIT_OPTIONAL_LOCKS": "0"}
        def runner(args, cwd):
            full_args = list(args[:1]) + ["--no-replace-objects"] + list(args[1:])
            return _sp.run(
                full_args, cwd=cwd, capture_output=True, text=True,
                timeout=30, check=False, env=env,
            )

    import os as _os
    out: list[dict] = []
    for f in file_paths:
        for github_repo, local_path in repo_paths.items():
            # Accept files OR directories: Bazel targets point at a
            # package directory, not a file. `git log -1 -- <dir>`
            # reports the most recent commit touching anything inside.
            full = _os.path.join(local_path, f)
            if not (_os.path.isfile(full) or _os.path.isdir(full)):
                continue
            # Use a delimiter unlikely to appear in commit messages so
            # the parse stays robust against weird subjects.
            sep = "\x1f"
            fmt = sep.join(["%H", "%an", "%ae", "%aI", "%s"])
            try:
                proc = runner(
                    ["git", "log", "-1", f"--format={fmt}", "--", f],
                    cwd=local_path,
                )
            except Exception:
                continue
            if proc.returncode != 0 or not proc.stdout.strip():
                continue
            parts = proc.stdout.strip().split(sep)
            if len(parts) < 5:
                continue
            sha, name, email, iso_date, subject = parts[0], parts[1], parts[2], parts[3], sep.join(parts[4:])
            out.append({
                "file": f,
                "repo": github_repo,
                "local_path": local_path,
                "author_name": name,
                "author_email": email,
                "commit": sha[:7],
                "committed_at": iso_date,
                "subject": subject,
            })
            # First repo that contained the file wins; no need to keep
            # probing the rest.
            break
    return out


_SUMMARY_STOPWORDS = {
    "a", "an", "the", "of", "for", "to", "and", "or", "in", "on",
    "is", "are", "was", "were", "be", "been", "with", "by", "from",
    "as", "at", "this", "that", "these", "those", "into", "via",
    "request", "issue", "fix", "bug", "task", "fix:", "fix(", "fix.",
    "implement", "investigate", "investigate:",
    # JIRA-noise words common across templated tickets:
    "failing", "target", "detected", "flaky", "test",
}


def _summary_keyword(summary: str) -> str:
    """Pull the longest meaningful contiguous word phrase out of a
    summary so JQL `summary ~ "<phrase>"` finds related tickets.

    For "Config Parity Request - module.subpackage.feature..." we
    want "module.subpackage.feature" or "Config Parity Request"
    (whichever survives stopword filtering AND is long enough).
    Returns "" when nothing meets the bar (very short summary, all
    stopwords).

    Heuristic: tokenise on whitespace, drop bracketed `[TAG]` prefixes
    and stopwords, then pick the longest contiguous run. Length-3+
    runs win to keep the phrase specific enough to filter out noise.
    """
    if not summary:
        return ""
    # Strip leading bracket tags like `[ALT-12345]`, `[CRITICAL]`.
    s = re.sub(r"^(\s*\[[^\]]+\])+\s*", "", summary)
    tokens = s.split()
    runs: list[list[str]] = []
    cur: list[str] = []
    for tok in tokens:
        # Token-level stopword check (case-insensitive); also drop
        # 1-2-char tokens to avoid matching on "of"/"in"/etc.
        bare = tok.strip(".:,()[]{}").lower()
        if not bare or bare in _SUMMARY_STOPWORDS or len(bare) <= 2:
            if cur:
                runs.append(cur)
                cur = []
            continue
        cur.append(tok)
    if cur:
        runs.append(cur)
    # Longest run wins; we want a phrase, not single words.
    if not runs:
        return ""
    longest = max(runs, key=lambda r: sum(len(x) for x in r))
    if len(longest) < 1:
        return ""
    phrase = " ".join(longest).strip(".:,()[]{}\"' ")
    # Need at least 8 chars total or the JQL match becomes too generic.
    return phrase if len(phrase) >= 8 else ""


def _find_similar_tickets(
    target: str, instance: dict, *,
    exclude_key: str = "",
    limit: int = 5,
    http_get=None, http_post=None,
) -> list[dict]:
    """Search the configured JIRA instance for recent tickets whose
    summary mentions the same Bazel target / test path.

    Used by `triage()` to surface "this same test was flaky 4 weeks
    ago and assigned to Alice -- ping her" context. Returns a slim
    projection: `{key, summary, status, assignee_email, components,
    created_at, url}` ordered newest-first. JIRA failures are
    swallowed (similar-ticket lookup is best-effort enrichment, not
    a hard requirement)."""
    if not target or not instance:
        return []
    # JIRA's `~` operator does fuzzy text match on summary. Quote the
    # target so the colon / slashes don't trip the parser. Drop the
    # `:label` part (configs vary across runs of the same suite).
    head = target.split(":", 1)[0]
    if not head:
        return []
    # Use EXACT phrase match (`~` with quoted string) to avoid
    # tokenisation pulling in unrelated tickets.
    jql = f'summary ~ "{head}" ORDER BY created DESC'
    try:
        issues = _jira.search_issues(
            base_url=instance["base_url"],
            auth_type=instance["auth_type"],
            email=instance["email"],
            api_token=instance["api_token"],
            jql=jql,
            max_results=max(limit, 1),
            http_get=http_get,
            http_post=http_post,
        )
    except Exception:
        return []
    out: list[dict] = []
    for issue in issues:
        rec = _normalise_issue(issue, instance)
        if not rec.get("key") or rec["key"] == exclude_key:
            continue
        components = []
        if rec.get("components"):
            try:
                import json as _json
                components = _json.loads(rec["components"])
            except Exception:
                components = []
        out.append({
            "key": rec["key"],
            "summary": rec["summary"],
            "status": rec["status"],
            "assignee_email": rec["assignee_email"],
            "components": components,
            "created_at": rec["created_at"],
            "url": rec["url"],
        })
        if len(out) >= limit:
            break
    return out


def _mode(items: list[str]) -> str:
    """Return the most-common non-empty entry, or '' when nothing
    qualifies. Tie-broken by first-occurrence so the result is
    deterministic across runs."""
    seen: dict[str, int] = {}
    order: list[str] = []
    for s in items:
        if not s:
            continue
        if s not in seen:
            order.append(s)
        seen[s] = seen.get(s, 0) + 1
    if not seen:
        return ""
    best = order[0]
    for s in order[1:]:
        if seen[s] > seen[best]:
            best = s
    return best


def _derive_owner_team(
    *, current_components: list[str],
    similar_tickets: list[dict],
    blame: list[dict],
    bazel_targets: list[str],
) -> str:
    """Best-effort guess at the owning team. Strongest-to-weakest:

      1. Components from the current ticket (JIRA's own answer).
      2. Most-common component across similar tickets.
      3. The Bazel target's first segment (e.g. `//widget/...` ->
         "widget"). Useful when JIRA components are empty (common
         on auto-filed ES tickets).
      4. Empty string -- no signal.
    """
    if current_components:
        return ", ".join(current_components)
    similar_components = []
    for t in similar_tickets:
        for c in t.get("components") or []:
            similar_components.append(c)
    if similar_components:
        m = _mode(similar_components)
        if m:
            return m
    # Bazel-target heuristic: the FIRST segment of `//widgets/foo/bar`
    # is usually the umbrella team (`widgets`, `widget`, `connect`).
    for tgt in bazel_targets:
        head = tgt.split("/", 1)[0] if "/" in tgt else ""
        if head:
            return f"{head} (inferred from Bazel path)"
    # Git blame can't reliably infer team without a person->team
    # directory; we deliberately don't guess from it.
    _ = blame
    return ""


def _derive_test_owner(
    *, current_assignee: str,
    similar_tickets: list[dict],
    blame: list[dict],
    self_email: str = "",
) -> str:
    """Best-effort guess at the engineer most likely to own the test:

      1. Current ticket's assignee, if set AND not the requesting
         user. The user is the one TRIAGING -- echoing their own
         email back is a tautology that produces "useless results".
      2. Most-common non-empty assignee across recent same-test
         tickets, excluding the requesting user.
      3. The git blame author for the most-recently-touched file
         in the referenced paths -- they edited the code last.
      4. Empty string.
    """
    if current_assignee and current_assignee != self_email:
        return current_assignee
    similar_assignees = [
        t.get("assignee_email") or "" for t in similar_tickets
    ]
    # Filter out self-echoes from similar-ticket aggregation too.
    if self_email:
        similar_assignees = [a for a in similar_assignees if a != self_email]
    m = _mode(similar_assignees)
    if m:
        return m
    if blame:
        return f"{blame[0]['author_name']} <{blame[0]['author_email']}>"
    # Last resort: surface the original assignee even if it's the
    # requesting user, so the panel isn't completely blank.
    if current_assignee:
        return current_assignee
    return ""


def triage(key: str, *, instance_name: str = "") -> dict | None:
    """Phase-1 triage analysis for a flaky-test (or any other) ticket.

    Bundles the per-ticket fields the user typically scans first:
    problem statement, owner team (assignee + JIRA components), files
    referenced in the description, labels. This is the data the
    `Triage` button on the Tickets page consumes; future iterations
    can layer git-blame on top by walking `files_referenced` against
    a configured repo path.

    Returns None when the ticket key isn't cached. The caller (route
    layer) maps that to a 404.
    """
    t = get_one(key, instance_name=instance_name)
    if not t:
        return None
    # `enrich_for_view` already parsed labels/components into lists,
    # so we can use them directly without re-parsing JSON columns.
    # Pull file/Bazel paths from BOTH the summary and the description.
    # EX-style 'Failing target detected: //x/y/z:label' tickets put the
    # only structured target in the summary line; description only has
    # URL-wrapped go-links that the extractor rejects.
    summary_text = t.get("summary", "") or ""
    description_text = t.get("description", "") or ""
    files_referenced = _extract_file_paths(summary_text + "\n" + description_text)
    blame = _git_blame_for_files(files_referenced)
    # Phase-3: search the SAME JIRA instance for recently-filed tickets
    # whose summary references the same Bazel target. Surfaces "the
    # same test was flaky last week, assigned to alice" context that
    # often leapfrogs the auto-tagged-but-empty assignee on ES tickets.
    similar_tickets: list[dict] = []
    bazel_targets = _BAZEL_TARGET_RE.findall(summary_text + " " + description_text)
    instance_for_search = None
    if t.get("instance_name"):
        for inst in list_instances():
            if inst["name"] == t["instance_name"]:
                instance_for_search = inst
                break
    if instance_for_search and bazel_targets:
        # Use the longest Bazel target prefix -- more specific = fewer
        # false positives when the suite name is generic.
        primary = max(bazel_targets, key=len)
        similar_tickets = _find_similar_tickets(
            f"//{primary}", instance_for_search,
            exclude_key=t.get("key", ""),
            limit=5,
        )
    elif instance_for_search and summary_text:
        # No Bazel target -> fall back to keyword search on the
        # summary so non-flaky tickets still get related-ticket
        # context. We pull the longest non-trivial token sequence
        # from the summary as the search phrase.
        keyword = _summary_keyword(summary_text)
        if keyword:
            similar_tickets = _find_similar_tickets(
                keyword, instance_for_search,
                exclude_key=t.get("key", ""),
                limit=5,
            )
    self_email = (
        instance_for_search.get("email", "")
        if instance_for_search else ""
    )
    most_likely_owner_team = _derive_owner_team(
        current_components=t.get("components", []) or [],
        similar_tickets=similar_tickets,
        blame=blame,
        bazel_targets=bazel_targets,
    )
    most_likely_test_owner = _derive_test_owner(
        current_assignee=t.get("assignee_email", "") or "",
        similar_tickets=similar_tickets,
        blame=blame,
        self_email=self_email,
    )
    return {
        "ticket": {
            "key": t.get("key", ""),
            "summary": t.get("summary", ""),
            "status": t.get("status", ""),
            "priority": t.get("priority", ""),
            "issue_type": t.get("issue_type", ""),
            "url": t.get("url", ""),
            "instance_name": t.get("instance_name", ""),
        },
        "problem": t.get("description", "") or t.get("summary", ""),
        "owner": {
            "assignee": t.get("assignee_email", ""),
            "reporter": t.get("reporter_email", ""),
            "components": t.get("components", []) or [],
            "labels": t.get("labels", []) or [],
            "project_key": t.get("project_key", ""),
        },
        "files_referenced": files_referenced,
        # Phase 2: git-blame each referenced file against the configured
        # local repos (`service.git.local_repo_paths`).
        "blame": blame,
        # Phase 3: similar-ticket search + aggregated owner/test-owner
        # guesses. `similar_tickets` is the raw list (lets the UI show
        # "this same test was flaky last week, assigned to..."); the
        # two `most_likely_*` fields are the synthesised top picks the
        # user can act on directly.
        "similar_tickets": similar_tickets,
        "most_likely_owner_team": most_likely_owner_team,
        "most_likely_test_owner": most_likely_test_owner,
    }


def track(key: str, *, instance_name: str = "",
          http_get=None) -> dict | None:
    """Ensure `key` is in the cache and return its view-enriched form.

    Resolution order:
      1. If a row already exists (with the matching instance, or any
         instance when `instance_name` is empty) -> return enriched
         immediately. No JIRA round-trip.
      2. Otherwise probe each configured instance via
         `adapters.jira.fetch_issue`. The first instance whose JIRA
         knows the key wins. Cache + return.
      3. If no instance recognises the key -> return None so the
         caller (route layer) can 404.

    This is what powers the "auto-track ticket numbers found on other
    pages" UX: a PR title carries `[EX-123]`, the user clicks it,
    and the cache fills in transparently.
    """
    if not key:
        raise ValueError("ticket key is required")

    # 1. Cache hit.
    if instance_name:
        row = app_state._db.get_ticket(key, instance_name=instance_name)
        if row:
            return enrich_for_view(row)
    else:
        # Walk every configured instance to find a cached match.
        for inst in list_instances():
            row = app_state._db.get_ticket(key, instance_name=inst["name"])
            if row:
                return enrich_for_view(row)

    # 2. JIRA probe.
    candidates = list_instances()
    if instance_name:
        candidates = [i for i in candidates if i["name"] == instance_name]
    if not candidates:
        # No configured instance to ask -> nothing to track.
        return None

    for inst in candidates:
        try:
            issue = _jira.fetch_issue(
                base_url=inst["base_url"],
                auth_type=inst["auth_type"],
                email=inst["email"],
                api_token=inst["api_token"],
                key=key, http_get=http_get,
            )
        except RuntimeError:
            # Auth or network error on this instance -> try the next
            # one; one broken JIRA shouldn't block the whole probe.
            continue
        if not issue:
            continue
        record = _normalise_issue(issue, inst)
        record["synced_at"] = _now_iso()
        app_state._db.upsert_ticket(**record)
        # Newly-cached row gets a created event so any open
        # TicketsPage repaints.
        _emit_ticket_event("created", record, [])
        row = app_state._db.get_ticket(key, instance_name=inst["name"])
        return enrich_for_view(row) if row else None

    return None


# ---- Ticket session ----

def session_name_for_ticket(key: str, instance_name: str = "") -> str:
    """tmux session name for a ticket. Includes the instance name so
    two JIRAs with overlapping keys (e.g. 'PROJ-1') don't collide on
    one tmux session."""
    if instance_name:
        return f"ticket-{instance_name}-{key}"
    return f"ticket-{key}"


def open_session_for_ticket(key: str, *, instance_name: str = "",
                            launcher=None,
                            custom_prompt: str = "",
                            paste=None) -> dict:
    """Spawn (or resume) a tmux + agent session bound to a ticket.

    The system-prompt is built from the cached ticket row so the agent
    starts with the ticket's summary / status / description in
    context. `launcher` is injectable for tests.

    `custom_prompt`, when provided, is sent into the session as the
    first user message *after* launch. This is what powers the
    action-button registry: a "Fix flaky test" button supplies a
    `/yh-fix-flaky-test` slash command (or any free-text prompt) and
    the executor pastes it once the agent is ready. `paste` is the
    transport injected for tests; production uses
    `adapters.tmux.paste_text` (bracketed paste, same code path the
    cron executor uses to avoid the `/yh-...` -> `/y` autocomplete
    truncation).
    """
    ticket = app_state._db.get_ticket(key, instance_name=instance_name)
    if not ticket:
        raise ValueError(
            f"ticket {key!r} not found in cache (instance={instance_name!r})"
        )

    session = session_name_for_ticket(key, instance_name=instance_name)
    bg_system = _build_ticket_system_prompt(ticket)

    if launcher is None:
        from adapters.tmux import launch_session_argv as _launch
        from adapters.tmux import session_exists as _exists
    else:
        _launch = launcher
        _exists = lambda _name: False  # tests assume always-new

    is_new = not _exists(session)
    if is_new:
        from . import agent as _agent
        argv = _agent.launch_argv(session, system_prompt=bg_system)
        _launch(session, "~", argv)
    if custom_prompt:
        # Run async-style: wait until the agent shows its prompt, then
        # paste. Best-effort -- a paste failure shouldn't fail the
        # session-open call (the user can still type the prompt).
        try:
            if paste is None:
                from adapters import tmux as _tmux
                if _tmux.wait_until_ready(session, timeout_secs=20):
                    _tmux.paste_text(session, custom_prompt)
                else:
                    _tmux.paste_text(session, custom_prompt)
            else:
                paste(session, custom_prompt)
        except Exception as e:
            print(f"[tickets] paste prompt for {key} failed: {e}",
                  flush=True)
    return {
        "session": session,
        "new": is_new,
        "ticket_key": key,
        "instance_name": instance_name,
        "prompt_sent": bool(custom_prompt),
    }


# ---- Phase-2 write actions: comment / list_transitions / transition ----
#
# Each helper resolves the JIRA instance for the given ticket key
# (looking it up first in the cached row's `instance_name`, then
# letting the caller override) so the user never has to think about
# which JIRA they're talking to. Auto-account routing falls out of the
# instance lookup -- email/token come from the configured instance.


def _resolve_instance_for_ticket(key: str, instance_name: str) -> dict:
    """Find the configured JIRA instance to use for `(instance_name,
    key)` write actions.

    Resolution order:
      1. If `instance_name` is given, look it up in the configured
         instances (raise ValueError when it doesn't exist).
      2. Otherwise, look the ticket up in the cache; use its stored
         `instance_name`.
      3. If neither resolves, raise ValueError so the caller surfaces
         a 404 (we never silently fall through to a "first-configured"
         heuristic, which would post comments to the wrong JIRA when
         the user has multiple instances).
    """
    instances = list_instances()
    if instance_name:
        match = next(
            (i for i in instances if i["name"] == instance_name), None,
        )
        if not match:
            raise ValueError(
                f"JIRA instance {instance_name!r} not configured"
            )
        return match
    # No explicit instance: scan every configured instance for the
    # ticket. `get_ticket` is by-(instance, key), so we walk.
    for inst in instances:
        if app_state._db.get_ticket(key, instance_name=inst["name"]):
            return inst
    raise ValueError(
        f"ticket {key!r} not found in any configured JIRA instance"
    )


def add_comment(key: str, body: str, *, instance_name: str = "",
                http_post=None) -> dict:
    """Post a comment on a JIRA ticket. Returns the JIRA response
    body (id, author, etc.) so the UI can render the new comment
    optimistically without a re-fetch."""
    if not body or not body.strip():
        raise ValueError("comment body is required")
    inst = _resolve_instance_for_ticket(key, instance_name)
    return _jira.add_comment(
        base_url=inst["base_url"],
        auth_type=inst["auth_type"],
        email=inst["email"],
        api_token=inst["api_token"],
        key=key, body=body.strip(),
        http_post=http_post,
    )


def list_transitions(key: str, *, instance_name: str = "",
                      http_get=None) -> list[dict]:
    """Return the transitions the calling user can apply to `key`.
    Frontend uses this to populate a dropdown / Resolve button."""
    inst = _resolve_instance_for_ticket(key, instance_name)
    return _jira.list_transitions(
        base_url=inst["base_url"],
        auth_type=inst["auth_type"],
        email=inst["email"],
        api_token=inst["api_token"],
        key=key, http_get=http_get,
    )


def transition_issue(key: str, transition_id: str, *,
                      instance_name: str = "", resolution: str = "",
                      comment: str = "", http_post=None) -> dict:
    """Apply a transition. Optionally set a `resolution` (Cloud
    requires this on Resolve) and attach a one-shot `comment` in the
    same payload."""
    if not transition_id:
        raise ValueError("transition_id is required")
    inst = _resolve_instance_for_ticket(key, instance_name)
    return _jira.transition_issue(
        base_url=inst["base_url"],
        auth_type=inst["auth_type"],
        email=inst["email"],
        api_token=inst["api_token"],
        key=key, transition_id=transition_id,
        resolution=resolution, comment=comment,
        http_post=http_post,
    )


def _build_ticket_system_prompt(ticket: dict) -> str:
    """Render a compact system-prompt block describing the ticket."""
    desc = ticket.get("description") or ""
    if len(desc) > 1500:
        desc = desc[:1500] + "\n...[truncated]"
    lines = [
        f"# JIRA Ticket: {ticket.get('key', '?')}",
        "",
        f"Summary: {ticket.get('summary', '')}",
        f"Status:  {ticket.get('status', '')}",
        f"Type:    {ticket.get('issue_type', '')}",
        f"Priority:{ticket.get('priority', '')}",
        f"URL:     {ticket.get('url', '')}",
    ]
    if ticket.get("instance_name"):
        lines.append(f"Instance:{ticket['instance_name']}")
    if desc:
        lines += ["", "## Description", desc]
    return "\n".join(lines)
