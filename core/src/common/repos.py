"""Repo allow-list helpers: turn rules (`org/repo` + `org/*`) into the
actual list of repos we have data for.

The Settings UI's Repos tab uses this to show users both views side
by side:

  Rules        =  what they configured (editable list)
  Resolved     =  the org/repo pairs the rules currently match,
                  derived live from the local prs table

For wildcard rules (`org/*`) we don't enumerate every repo in the org
on GitHub -- that would be slow and most aren't relevant. Instead we
list the repos in that org that we have at least one PR for, since
those are the ones the system has actually exercised.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Iterable

import app_state
from . import settings as _settings


_PR_URL_RE = re.compile(r"github\.com/([^/]+)/([^/]+)/")


def get_rules() -> list[str]:
    """Return the configured allow-list rules.

    Order: settings table -> hardcoded fallback in `adapters/github.py`.
    Rules are normalised to a sorted list so the UI shows a stable
    order.
    """
    raw = _settings.get_value(_settings.KEY_GITHUB_ALLOWED_REPOS)
    if isinstance(raw, list) and raw:
        rules = [str(r) for r in raw if isinstance(r, str)]
    else:
        rules = list(app_state.ALLOWED_REPOS)
    return sorted(set(rules))


def _split_rules(rules: Iterable[str]) -> tuple[list[str], list[str]]:
    """Partition rules into (explicit, wildcard_orgs).

    Explicit  -> [`org/repo`, ...]
    Wildcards -> [`org`, ...]  (the trailing `/*` stripped off)
    """
    explicit: list[str] = []
    wildcards: list[str] = []
    for r in rules:
        if r.endswith("/*"):
            wildcards.append(r[:-2])
        elif "/" in r:
            explicit.append(r)
    return explicit, wildcards


def _pr_repo_counts() -> dict[str, int]:
    """Count distinct `org/repo` occurrences across the prs table.

    Parsed from the URL because the table doesn't have an explicit
    `org/repo` column (only `repo` short-name lives there for
    historical reasons -- see schema in eva_db.py).
    """
    counts: dict[str, int] = {}
    try:
        rows = app_state._db._conn.execute("SELECT url FROM prs").fetchall()
    except Exception:
        return counts
    for (url,) in rows:
        if not url:
            continue
        m = _PR_URL_RE.search(url)
        if not m:
            continue
        key = f"{m.group(1)}/{m.group(2)}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def resolve() -> dict:
    """Return the rule->repo expansion plus per-repo PR counts.

    Shape:
      {
        "rules": ["acme/widgets", "my-org/*"],
        "fork_to_upstream": {"myuser/widgets": "acme/widgets"},
        "resolved": [
          {"repo": "acme/widgets", "source": "rule", "pr_count": 154},
          {"repo": "my-org/runtime", "source": "wildcard",
           "wildcard": "my-org/*", "pr_count": 41},
          ...
        ],
      }

    `resolved` is sorted by PR count descending so the most-active
    repos surface first.
    """
    rules = get_rules()
    explicit, wildcard_orgs = _split_rules(rules)
    counts = _pr_repo_counts()

    seen: set[str] = set()
    resolved: list[dict] = []

    for full in explicit:
        resolved.append({
            "repo": full,
            "source": "rule",
            "pr_count": counts.get(full, 0),
        })
        seen.add(full)

    # For each wildcard org, surface every distinct PR-repo we've seen
    # under that org (excluding any that's already in `explicit`).
    for org in wildcard_orgs:
        prefix = org + "/"
        sub_repos = sorted(
            (r for r in counts if r.startswith(prefix) and r not in seen),
            key=lambda r: -counts[r],
        )
        for full in sub_repos:
            resolved.append({
                "repo": full,
                "source": "wildcard",
                "wildcard": f"{org}/*",
                "pr_count": counts.get(full, 0),
            })
            seen.add(full)

    resolved.sort(key=lambda r: (-r["pr_count"], r["repo"]))

    fork_to_upstream_raw = _settings.get_value(
        _settings.KEY_GITHUB_FORK_TO_UPSTREAM)
    if isinstance(fork_to_upstream_raw, dict):
        fork_to_upstream = {
            str(k): str(v) for k, v in fork_to_upstream_raw.items()
        }
    else:
        fork_to_upstream = dict(app_state.FORK_TO_UPSTREAM)

    return {
        "rules": rules,
        "fork_to_upstream": fork_to_upstream,
        "resolved": resolved,
    }


def detect_forks() -> dict:
    """Scan every loaded gh CLI account for forks whose upstream is
    covered by the configured `allowed_repos` (exact or `org/*`).

    GitHub's repo metadata already carries each fork's `parent`, so
    auto-filling `fork_to_upstream` only needs a single API call per
    account -- no scraping, no per-fork lookups.

    Shape:
      {
        "detected": {"alice/widgets": "acme/widgets", ...},
        "scanned_accounts": ["alice", "alice-work"],
        "errors": [{"account": "alice-work", "message": "..."}]
      }

    Forks whose parent is NOT in the allow-list are dropped silently
    (the user only sees relevant matches in the suggested map).
    """
    from adapters import github as _gh

    detected: dict[str, str] = {}
    scanned: list[str] = []
    errors: list[dict] = []

    for login, token in _gh._gh_tokens.items():
        scanned.append(login)
        if not token:
            errors.append({"account": login, "message": "no token loaded"})
            continue
        env = {**os.environ, "GH_TOKEN": token}
        try:
            r = subprocess.run(
                ["gh", "repo", "list", login, "--fork",
                 "--json", "nameWithOwner,parent", "--limit", "200"],
                capture_output=True, text=True, timeout=20, env=env,
            )
        except Exception as e:  # noqa: BLE001
            errors.append({"account": login, "message": str(e)})
            continue
        if r.returncode != 0:
            errors.append({
                "account": login,
                "message": (r.stderr or "").strip()[:300] or "gh exit non-zero",
            })
            continue
        try:
            rows = json.loads(r.stdout)
        except json.JSONDecodeError:
            errors.append({"account": login, "message": "invalid JSON from gh"})
            continue
        for row in rows:
            fork = row.get("nameWithOwner") or ""
            parent = row.get("parent") or {}
            if not isinstance(parent, dict):
                continue
            owner = (parent.get("owner") or {}).get("login") or ""
            name = parent.get("name") or ""
            if not fork or not owner or not name:
                continue
            upstream = f"{owner}/{name}"
            if _gh.is_repo_allowed(upstream):
                detected[fork] = upstream

    return {
        "detected": detected,
        "scanned_accounts": scanned,
        "errors": errors,
    }
