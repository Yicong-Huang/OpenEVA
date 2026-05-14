"""GitHub adapter: the single place Eva shells out to the `gh` CLI.

Exposes:
  - Allow-list config (ALLOWED_REPOS, ALLOWED_ORGS, FORK_TO_UPSTREAM)
    + helpers to test whether a repo is in scope.
  - Multi-account token loading read from ~/.config/gh/hosts.yml.
  - `gh_run` / `gh_run_json` / `gh_run_or_raise` / `gh_run_async` -- all
    CLI invocations go through one of these so tests only need to mock a
    single surface.

Note on module state:
  `_gh_tokens` is a module-level dict loaded at import time. Any caller
  that needs to override it for tests should monkeypatch this module
  (not `app_state`), since `app_state` only re-exports the binding for
  back-compat.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from utils import pr_number_from_url


# ---------------------------------------------------------------------------
# Allow-list config
# ---------------------------------------------------------------------------

# Repos to track PRs from. Supports "org/*" wildcards for entire orgs.
# Empty by default -- each install configures its own list via the
# Settings UI (`service.github.allowed_repos`). The startup hook in
# `app_state._apply_repo_overrides_from_settings` mutates this set in
# place from the DB before any route handler imports it.
ALLOWED_REPOS: set[str] = set()

# Map of fork repo -> upstream repo. Used by `is_repo_allowed` so PRs
# on a maintainer's personal fork still resolve to an allowed upstream.
# Empty by default; populate via Settings (`service.github.fork_to_upstream`).
FORK_TO_UPSTREAM: dict[str, str] = {}

# Derived: orgs extracted from wildcard entries.
ALLOWED_ORGS = {r.split("/")[0] for r in ALLOWED_REPOS if r.endswith("/*")}


def _parse_pr_number(url: str):
    """Extract PR number from a GitHub PR URL. Returns int or None."""
    return pr_number_from_url(url)


def is_repo_allowed(full_repo: str) -> bool:
    """Check if a repo (or its upstream) is in the allowed list.

    Supports exact match ("acme/widgets") and org wildcards
    ("test-org/*"). Resolves forks via FORK_TO_UPSTREAM before checking
    so e.g. "myuser/widgets" (a personal fork) maps to "acme/widgets"
    (its upstream) and passes the allow list.
    """
    if not full_repo:
        return False
    upstream = FORK_TO_UPSTREAM.get(full_repo, full_repo)
    if upstream in ALLOWED_REPOS:
        return True
    org = upstream.split("/")[0]
    return (org + "/*") in ALLOWED_REPOS


# ---------------------------------------------------------------------------
# Token loading + per-repo account selection
# ---------------------------------------------------------------------------

def _load_gh_tokens() -> dict:
    """Load GitHub login -> token map from gh CLI config.

    Modern gh (>=2.40) stores OAuth tokens in the OS keyring and leaves
    each user entry in `~/.config/gh/hosts.yml` empty (YAML null). Older
    gh wrote the token inline as `oauth_token`. We support both:

      - Keyring storage: the value is None / {}, we record the login
        with an empty-string token. Callers that need an actual bearer
        token must fall back to `gh auth token --user <login>` or to
        the `GITHUB_TOKEN` env var.
      - Inline storage: read `oauth_token` straight off the user info.
    """
    try:
        with open(Path.home() / ".config" / "gh" / "hosts.yml") as f:
            data = yaml.safe_load(f) or {}
        users = (data.get("github.com") or {}).get("users") or {}
        result: dict[str, str] = {}
        for u, info in users.items():
            if not u:
                continue
            if isinstance(info, dict):
                result[u] = info.get("oauth_token", "") or ""
            else:
                # `info` is None (keyring storage) or some unexpected
                # shape -- still record the login so multi-account
                # routing and the setup-status check can see it.
                result[u] = ""
        return result
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


# Loaded once at import time so hot-path callers (every PR sync, every
# notification) don't re-parse the YAML. Tests can monkeypatch either
# the dict contents (`_gh_tokens.clear(); _gh_tokens.update(...)`) or
# the binding (`monkeypatch.setattr(adapters.github, "_gh_tokens",
# {...})`). For UI flows that must observe live `gh auth login` /
# `gh auth logout` results without a server restart -- typically the
# setup-status route -- call `refresh_gh_tokens()` first.
_gh_tokens = _load_gh_tokens()


def refresh_gh_tokens() -> dict:
    """Re-read `~/.config/gh/hosts.yml` and mutate `_gh_tokens` in
    place. Returns the refreshed dict so callers can introspect what
    changed. Mutation (vs. rebinding) keeps existing `from
    adapters.github import _gh_tokens` references valid."""
    fresh = _load_gh_tokens()
    _gh_tokens.clear()
    _gh_tokens.update(fresh)
    return _gh_tokens


# Account-rules are loaded once at startup (same pattern as
# ALLOWED_REPOS / FORK_TO_UPSTREAM): the open-source user fills in
# the Settings UI's "GitHub account rules" section, then a server
# restart re-reads. We keep an in-memory list rather than hitting the
# settings table on every gh subprocess call -- this function gets
# called per-PR-fetch and per-notification, hot path.
_account_rules: list[dict] = []


def _default_account_fallback() -> str:
    """Final fallback when no `service.github.account_rules` are
    configured. Returns the first gh CLI account loaded from the
    user's `~/.config/gh/hosts.yml`, or empty string when none exist
    (gh subprocess will then surface its own auth-required error).
    No personal-name string is hardcoded here -- this is what makes
    the binary safe to ship."""
    if _gh_tokens:
        return next(iter(_gh_tokens))
    return ""


def gh_account_for_repo(repo: str) -> str:
    """Return the gh CLI account to use for a given repo.

    Settings rules win; each rule is `{match, account}` and we walk
    in order returning the first match (an empty `match` is a
    catch-all). With no rules configured, fall through to the first
    available gh CLI account (no personal string baked in).
    """
    for rule in _account_rules:
        match = rule.get("match", "")
        account = rule.get("account", "")
        if not account:
            continue
        if not match or match in repo:
            return account
    return _default_account_fallback()


def _build_repo_authors() -> dict:
    """Build {repo_or_org: gh_account} mapping for PR sync search.

    For explicit repos ("acme/widgets"), searches that repo.
    For org wildcards ("my-org/*"), searches the entire org.
    Also adds upstream repos from FORK_TO_UPSTREAM.

    The key format tells the sync code how to search:
      - "owner:my-org"    -> gh search prs --owner my-org
      - "acme/widgets"    -> gh search prs --repo acme/widgets
    """
    result = {}
    for repo in ALLOWED_REPOS:
        if repo.endswith("/*"):
            org = repo[:-2]
            key = "owner:" + org
            result[key] = gh_account_for_repo(org + "/x")
        else:
            result[repo] = gh_account_for_repo(repo)
    # Add upstream repos from FORK_TO_UPSTREAM (if not already covered by a wildcard).
    for upstream in FORK_TO_UPSTREAM.values():
        org = upstream.split("/")[0]
        org_key = "owner:" + org
        if upstream not in result and org_key not in result:
            result[upstream] = gh_account_for_repo(upstream)
    return result


# ---------------------------------------------------------------------------
# gh CLI runners
# ---------------------------------------------------------------------------

def gh_run(args: list, repo: str = "", timeout: int = 20,
           input_text: str | None = None):
    """Run a gh CLI command with the correct account token via GH_TOKEN env var.

    This is the ONE place that shells out to the `gh` binary. Wrappers
    (`gh_run_json`, `gh_run_or_raise`, `gh_run_async`) live in
    `app_state` so tests that monkeypatch `app_state.gh_run` see the
    override through those wrappers via the re-exported binding.

    `input_text` is piped to the subprocess's stdin. Use it for
    `gh api ... --input -` calls that ship a JSON body too rich for
    `-f`/`-F` flag pairs (e.g. an array of nested objects)."""
    env = dict(os.environ)
    if repo:
        account = gh_account_for_repo(repo)
        token = _gh_tokens.get(account, "")
        if token:
            env["GH_TOKEN"] = token
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, env=env,
        input=input_text,
    )
