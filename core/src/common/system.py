"""System health and stats core logic."""

import json as _json
import re as _re
import sqlite3
import subprocess
import time as _time
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import app_state


# Convenience re-exports of the cert framework so routes can keep
# importing from `system` -- the actual definitions live in
# `cert`, where extensions register their providers.
from .cert import (  # noqa: F401
    CertProviderBase as CertProvider,
    CERT_WARNING_SECS,
    get_certs,
    renew_cert,
)


# ====================================================================
# Usage
# ====================================================================

_USAGE_DB_PATH = app_state._USAGE_DB_PATH


def _init_usage_db():
    with sqlite3.connect(str(app_state._USAGE_DB_PATH)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS usage_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            daily REAL,
            weekly REAL,
            monthly REAL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_history(ts)")


_init_usage_db()


def _save_usage_record(data):
    try:
        daily = float(data["daily"].replace(",", "")) if data.get("daily") else None
        weekly = float(data["weekly"].replace(",", "")) if data.get("weekly") else None
        monthly = float(data["monthly"].replace(",", "")) if data.get("monthly") else None
        with sqlite3.connect(str(app_state._USAGE_DB_PATH)) as conn:
            conn.execute(
                "INSERT INTO usage_history (ts, daily, weekly, monthly) VALUES (?, ?, ?, ?)",
                (datetime.now().strftime("%Y-%m-%dT%H:%M:%S"), daily, weekly, monthly),
            )
    except (ValueError, sqlite3.Error):
        pass


_usage_cache = {"data": None, "ts": 0}


def _fetch_usage():
    """Fetch usage via the agent adapter, persist it, and stamp updated_at.

    The subprocess + parsing live in the Agent layer
    (`agent.get_active_agent().fetch_usage()`); this
    function owns the DB write (`_save_usage_record`) and the
    wall-clock stamp callers expect to see in the UI."""
    from . import agent as _agent
    data = _agent.fetch_usage(days=1)
    if data is None:
        return None
    if data["daily"]:
        _save_usage_record(data)
        data["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    return data


def get_usage():
    """Get the agent's usage summary.

    The scheduler's `refresh_usage_once` keeps the cache warm in the
    background (120s interval), so most requests are a pure dict read.
    The cold-start path only runs on the very first request before the
    scheduler has ticked."""
    if _usage_cache["data"]:
        return _usage_cache["data"]

    # Cold start: synchronous fetch. Subsequent requests hit the warm
    # cache the scheduler maintains.
    data = _fetch_usage()
    if data:
        _usage_cache["data"] = data
        _usage_cache["ts"] = _time.time()
    return data or {"daily": None, "weekly": None, "monthly": None, "tier": None}


def get_usage_history(days=7):
    """Return usage history from SQLite. Returns {history, total_records}."""
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with sqlite3.connect(str(app_state._USAGE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM usage_history WHERE ts >= ? ORDER BY ts ASC",
            (since,),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM usage_history").fetchone()[0]
    return {
        "history": [dict(r) for r in rows],
        "total_records": total,
    }


# ====================================================================
# Live Stats (from routes/events.py)
# ====================================================================

_live_stats_cache = {"data": None, "ts": 0}


def _build_accounts():
    """Build [{user, repos}] from config for live stats PR counting.

    Includes "owner:org" entries -- _fetch_live_stats handles them
    by using --owner instead of --repo for gh search.
    """
    repo_authors = app_state._build_repo_authors()
    by_user = {}
    for repo, user in repo_authors.items():
        by_user.setdefault(user, []).append(repo)
    return [{"user": u, "repos": rs} for u, rs in by_user.items()]


# Primary OSS repo for contributor rank tracking (first explicit repo).
# Resolved lazily so settings-driven changes to ALLOWED_REPOS at startup
# (or test-fixture overrides at test time) take effect without requiring
# a module reload.
def _contributor_repo() -> str | None:
    return next(
        (r for r in app_state.ALLOWED_REPOS
         if "/" in r and not r.endswith("/*")),
        None,
    )


def _contributor_user() -> str | None:
    repo = _contributor_repo()
    return app_state.gh_account_for_repo(repo) if repo else None


def _short_repo_names() -> dict[str, str]:
    """Map long repo names to compact display labels.

    Driven by the `ui.repo_short_names` setting (JSON map of
    `{full_repo_name: short_label}`), so different installs can
    abbreviate their own long-named repos without code changes.
    Empty by default; the UI just shows full names when unset.
    """
    from . import settings as _settings
    val = _settings.get_value("ui.repo_short_names", default=None)
    if isinstance(val, dict):
        return val
    if isinstance(val, str) and val.strip():
        try:
            import json as _json
            parsed = _json.loads(val)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, _json.JSONDecodeError):
            return {}
    return {}


def _count_open_prs_for_account(acct):
    """Query gh for every repo/owner in one account, return (count_per_repo, total).

    The account's "repos" entries can be either "org/repo" (single repo) or
    "owner:org" (search across all repos owned by org). The second form returns
    a count grouped by repo; the first form returns just a single count.
    """
    open_prs = {}
    total = 0
    for repo_or_owner in acct["repos"]:
        try:
            if repo_or_owner.startswith("owner:"):
                org = repo_or_owner[6:]
                search_arg = ["--owner", org]
                gh_hint = org + "/main"
                json_fields = "number,repository"
            else:
                search_arg = ["--repo", repo_or_owner]
                gh_hint = repo_or_owner
                json_fields = "number"
            items = app_state.gh_run_json(
                ["gh", "search", "prs"] + search_arg + ["--author", acct["user"],
                 "--state", "open", "--limit", "100", "--json", json_fields],
                repo=gh_hint, timeout=15,
            )
            if items is None:
                continue
            if repo_or_owner.startswith("owner:"):
                for item in items:
                    repo_info = item.get("repository", {})
                    full_name = repo_info.get("nameWithOwner", "") if isinstance(repo_info, dict) else str(repo_info)
                    label = full_name.split("/")[-1] if "/" in full_name else full_name
                    if not label:
                        label = org
                    label = _short_repo_names().get(label, label)
                    open_prs[label] = open_prs.get(label, 0) + 1
                    total += 1
            else:
                count = len(items)
                label = repo_or_owner.split("/")[-1]
                label = _short_repo_names().get(label, label)
                open_prs[label] = open_prs.get(label, 0) + count
                total += count
        except Exception:
            pass
    return open_prs, total


def _fetch_contributor_rank(repo, user):
    """Return (rank, contributions) for `user` in the first 500 contributors
    of `repo`, or (None, None) when not found / on error."""
    try:
        for page in range(1, 6):
            result = app_state.gh_run(
                ["gh", "api", f"repos/{repo}/contributors?per_page=100&page={page}",
                 "--jq", '.[] | "\\(.login) \\(.contributions)"'],
                repo=repo, timeout=15,
            )
            if result.returncode != 0:
                return None, None
            lines = result.stdout.strip().split("\n")
            for idx, line in enumerate(lines):
                parts = line.split()
                if len(parts) >= 2 and parts[0] == user:
                    return (page - 1) * 100 + idx + 1, int(parts[1])
    except Exception:
        pass
    return None, None


def _fetch_contributor_total(repo):
    """Read Link header from gh's contributor API to extract total-pages."""
    try:
        result = app_state.gh_run(
            ["gh", "api", f"repos/{repo}/contributors?per_page=1", "-i"],
            repo=repo, timeout=10,
        )
        if result.returncode != 0:
            return None
        for line in (result.stdout + result.stderr).split("\n"):
            if "link:" in line.lower() and "page=" in line:
                pages = _re.findall(r'page=(\d+)', line)
                if pages:
                    return int(pages[-1])
    except Exception:
        pass
    return None


def _fetch_live_stats():
    """Fetch live open PR counts and contributor rank via gh CLI."""
    stats = {"open_prs": {}, "contributor_rank": None,
             "contributor_contributions": None, "contributor_total": None}

    total_open = 0
    for acct in _build_accounts():
        per_repo, acct_total = _count_open_prs_for_account(acct)
        for label, count in per_repo.items():
            stats["open_prs"][label] = stats["open_prs"].get(label, 0) + count
        total_open += acct_total
    stats["open_prs"]["total"] = total_open

    repo = _contributor_repo()
    user = _contributor_user()
    if not repo or not user:
        return stats

    rank, contributions = _fetch_contributor_rank(repo, user)
    if rank is not None:
        stats["contributor_rank"] = rank
        stats["contributor_contributions"] = contributions
        # Include the resolved repo so the UI can build the link
        # generically (no hardcoded `acme/widgets` in the frontend).
        stats["contributor_repo"] = repo

    total = _fetch_contributor_total(repo)
    if total is not None:
        stats["contributor_total"] = total

    return stats


def get_live_stats(refresh=False):
    """Get open PR counts + Widgets rank. Returns dict."""
    now = _time.time()
    if not refresh and _live_stats_cache["data"] and now - _live_stats_cache["ts"] < 120:
        return _live_stats_cache["data"]

    def do_refresh():
        data = _fetch_live_stats()
        if data:
            _live_stats_cache["data"] = data
            _live_stats_cache["ts"] = _time.time()

    if not _live_stats_cache["data"]:
        _live_stats_cache["data"] = _fetch_live_stats()
        _live_stats_cache["ts"] = _time.time()
    else:
        t = threading.Thread(target=do_refresh, daemon=True)
        t.start()

    return _live_stats_cache["data"]


# ====================================================================
# Work Stats (from routes/events.py)
# ====================================================================

_workstats_cache = {"data": None, "ts": 0}


def _repo_from_url(url):
    """Extract short repo name from a GitHub PR URL.

    Returns the path segment after the org (e.g.
    `https://github.com/acme/widgets/pull/123` -> `widgets`). Returns
    empty string when the URL doesn't match the expected
    `github.com/<owner>/<repo>/...` shape so the caller can drop the
    row.

    Long display names that the rest of the UI shortens (see
    `_short_repo_names`) are normalised here too so the workstats
    aggregation keys match the live-stats keys.
    """
    if not url or "github.com/" not in url:
        return ""
    parts = url.split("github.com/", 1)[1].split("/")
    if len(parts) < 2 or not parts[1]:
        return ""
    return _short_repo_names().get(parts[1], parts[1])


def _fiscal_quarter(ts):
    """Convert ISO timestamp to a fiscal quarter string.

    FY quarters: Q1=Feb-Apr, Q2=May-Jul, Q3=Aug-Oct, Q4=Nov-Jan.
    """
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    m, y = dt.month, dt.year
    if 2 <= m <= 4:
        return f"Q1 FY{y + 1 - 2000}"
    elif 5 <= m <= 7:
        return f"Q2 FY{y + 1 - 2000}"
    elif 8 <= m <= 10:
        return f"Q3 FY{y + 1 - 2000}"
    elif m >= 11:
        return f"Q4 FY{y + 1 - 2000}"
    else:  # January
        return f"Q4 FY{y - 2000}"


def _my_gh_authors() -> set[str]:
    """Return the set of gh login names that count as 'me'.

    Derived from the per-repo author config so adding a new account in
    config automatically extends the workstats filter without code
    changes. Used to strictly exclude bot/coworker PRs (e.g.
    `app/service-safe-team`, review-watch entries) from the PR plugin's
    self-progress charts.
    """
    return {a["user"] for a in _build_accounts()}


def _primary_repo_for_workstats() -> str:
    """Repo name (short label) that the workstats trendline overlays
    on top of the "total" series. Setting `service.plugins.pr_primary_repo`
    controls it; default empty string -> overlay series is all zeros."""
    try:
        from . import settings as _settings
        v = _settings.get_value(
            "service.plugins.pr_primary_repo", default=None)
        if isinstance(v, str):
            return v
    except Exception:
        pass
    return ""


def _compute_workstats():
    """Compute PR stats from the local eva.db (no external dependencies).

    Aggregates merged/closed PRs that I authored into fiscal-year
    quarter buckets + ISO-week bucket counts. PRs with an unknown or
    other-user author are strictly excluded so the chart reflects my
    own merged work, not review-watch entries or bot PRs that happen
    to live in the local mirror. Returns None if the DB query fails.

    Output schema:
      {
        "quarters": [
          {"period": "Q3 FY26", "by_repo": {"widgets": 8, ...}, "total": N}
        ],
        "all_time": {"widgets": N, ..., "total": M},
        "weekly": [int, ...],
        "weekly_primary": [int, ...],  # parallel to weekly,
                                       # filtered to `pr_primary_repo`
      }

    Repo names are derived dynamically from PR URLs (no hardcoded
    enumeration) so any user's GitHub install populates this view
    without code change.
    """
    authors = _my_gh_authors()
    empty = {"quarters": [], "all_time": {},
             "weekly": [], "weekly_primary": []}
    if not authors:
        return empty
    placeholders = ",".join("?" * len(authors))
    try:
        conn = app_state._db._conn
        rows = conn.execute(
            f"SELECT url, last_updated FROM prs "
            f"WHERE status IN ('merged', 'closed') AND last_updated != '' "
            f"AND author IN ({placeholders})",
            tuple(authors),
        ).fetchall()
    except Exception:
        return None

    primary = _primary_repo_for_workstats()
    quarter_data: dict[str, dict] = {}
    # Weekly buckets: one aligned pair of series so the PR plugin can draw
    # the primary repo vs total on the same trendline. Keyed by ISO
    # "YYYY-WW" so all repos share the same axis.
    weekly_total: dict[str, int] = {}
    weekly_primary: dict[str, int] = {}

    for url, ts in rows:
        repo = _repo_from_url(url)
        if not repo:
            continue
        q = _fiscal_quarter(ts)
        if not q:
            continue

        if q not in quarter_data:
            quarter_data[q] = {"period": q, "by_repo": {}, "total": 0}
        bucket = quarter_data[q]["by_repo"]
        bucket[repo] = bucket.get(repo, 0) + 1
        quarter_data[q]["total"] += 1

        # Weekly: ISO week. `ts` must already parse cleanly because
        # `_fiscal_quarter(ts)` returned a non-None `q` above using the
        # same fromisoformat call.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        week_key = dt.strftime("%Y-%W")
        weekly_total[week_key] = weekly_total.get(week_key, 0) + 1
        if primary and repo == primary:
            weekly_primary[week_key] = weekly_primary.get(week_key, 0) + 1

    def _quarter_sort_key(q):
        p = q["period"]  # e.g. "Q3 FY26"
        qn = int(p[1])
        fy = int(p[-2:])
        return (fy, qn)
    quarters = sorted(quarter_data.values(),
                      key=_quarter_sort_key, reverse=True)
    week_keys = sorted(weekly_total.keys())
    weekly = [weekly_total[k] for k in week_keys]
    weekly_primary_series = [weekly_primary.get(k, 0) for k in week_keys]

    all_time: dict[str, int] = {}
    total_all = 0
    for q in quarters:
        for repo, cnt in q["by_repo"].items():
            all_time[repo] = all_time.get(repo, 0) + cnt
        total_all += q["total"]
    all_time["total"] = total_all

    return {
        "quarters": quarters,
        "all_time": all_time,
        "weekly": weekly,
        "weekly_primary": weekly_primary_series,
    }


def get_workstats(refresh=False):
    """Get quarterly PR stats from local DB. Caches for 5 min."""
    now = _time.time()
    if not refresh and _workstats_cache["data"] and now - _workstats_cache["ts"] < 300:
        return _workstats_cache["data"]

    result = _compute_workstats()
    if result:
        _workstats_cache["data"] = result
        _workstats_cache["ts"] = now
    elif not _workstats_cache["data"]:
        _workstats_cache["data"] = {
            "quarters": [], "all_time": {},
            "weekly": [], "weekly_primary": [],
        }

    return _workstats_cache["data"]


# ====================================================================
# Setup status -- "is Eva configured enough to do anything useful?"
# ====================================================================

def get_setup_status() -> dict:
    """Health check for first-time setup. Each entry has a stable `id`
    so the UI can render specific remediation hints. `ok=False` on any
    entry means the feature it gates is degraded -- e.g. no gh CLI
    means every PR sync call will fail.
    """
    from adapters import github as _gh
    from . import settings as _settings

    checks: list[dict] = []

    # 1. gh binary on PATH.
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True,
                           text=True, timeout=5)
        gh_ok = r.returncode == 0
        gh_detail = (r.stdout.splitlines() or [""])[0] if gh_ok \
            else (r.stderr or "gh exited non-zero").strip()[:200]
    except FileNotFoundError:
        gh_ok = False
        gh_detail = "gh binary not found on PATH"
    except Exception as e:  # noqa: BLE001
        gh_ok = False
        gh_detail = f"failed to invoke gh: {e}"
    checks.append({
        "id": "gh_binary",
        "label": "GitHub CLI installed",
        "ok": gh_ok,
        "detail": gh_detail,
        "hint": (
            "Install gh from https://cli.github.com (e.g. "
            "`brew install gh` or `apt install gh`)."
            if not gh_ok else ""
        ),
    })

    # 2. tmux on PATH. Project Manager + every task/review session
    # runs inside a tmux pane (see core/src/adapters/tmux.py), so the
    # entire AI-session surface is broken without it.
    try:
        r = subprocess.run(["tmux", "-V"], capture_output=True,
                           text=True, timeout=5)
        tmux_ok = r.returncode == 0
        tmux_detail = (r.stdout or r.stderr).strip()[:200] if tmux_ok \
            else (r.stderr or "tmux exited non-zero").strip()[:200]
    except FileNotFoundError:
        tmux_ok = False
        tmux_detail = "tmux binary not found on PATH"
    except Exception as e:  # noqa: BLE001
        tmux_ok = False
        tmux_detail = f"failed to invoke tmux: {e}"
    checks.append({
        "id": "tmux_binary",
        "label": "tmux installed",
        "ok": tmux_ok,
        "detail": tmux_detail,
        "hint": (
            "Install tmux: `brew install tmux` (macOS) or "
            "`apt install tmux` (Debian/Ubuntu). Eva runs every AI "
            "session in a tmux pane -- Project Manager, task sessions, "
            "and review sessions all need it."
            if not tmux_ok else ""
        ),
    })

    # 3. At least one authenticated gh account.
    # Re-read hosts.yml on every status call so the user can run
    # `gh auth login` while the server is up and see the check go
    # green on next refresh (no restart needed).
    tokens = _gh.refresh_gh_tokens()
    accounts = sorted(tokens.keys())
    accounts_ok = bool(accounts)
    checks.append({
        "id": "gh_accounts",
        "label": "GitHub account(s) authenticated",
        "ok": accounts_ok,
        "detail": (f"{len(accounts)} account(s): {', '.join(accounts)}"
                   if accounts else "No accounts in ~/.config/gh/hosts.yml"),
        "hint": (
            "Run `gh auth login`. For multiple accounts, run it once "
            "per account."
            if not accounts_ok else ""
        ),
    })

    # 4. allowed_repos configured (yaml seed or Settings UI).
    raw_repos = _settings.get_value(
        _settings.KEY_GITHUB_ALLOWED_REPOS, default=None)
    repos_list = raw_repos if isinstance(raw_repos, list) else []
    repos_ok = bool(repos_list)
    checks.append({
        "id": "allowed_repos",
        "label": "Repos to track configured",
        "ok": repos_ok,
        "detail": (f"{len(repos_list)} rule(s): {', '.join(str(r) for r in repos_list[:5])}"
                   + ("..." if len(repos_list) > 5 else "")
                   if repos_ok else "Empty allow-list -- no PRs will sync"),
        "hint": (
            "Settings -> Repos -> add at least one repo (e.g. "
            "`acme/widgets`) or org wildcard (e.g. `my-org/*`)."
            if not repos_ok else ""
        ),
    })

    # 5. account_rules required only when 2+ tokens are loaded.
    if len(accounts) >= 2:
        raw_rules = _settings.get_value(
            _settings.KEY_GITHUB_ACCOUNT_RULES, default=None)
        rules_list = raw_rules if isinstance(raw_rules, list) else []
        rules_ok = bool(rules_list)
        checks.append({
            "id": "account_rules",
            "label": "Multi-account routing rules",
            "ok": rules_ok,
            "detail": (f"{len(rules_list)} rule(s) configured"
                       if rules_ok else
                       f"{len(accounts)} accounts loaded but no rules -- "
                       "every repo will route to the first account, which "
                       "breaks PR sync for repos owned by the other account"),
            "hint": (
                "Settings -> Repos -> GitHub account rules. Add one rule "
                "per account; first match wins. The last rule should have "
                "an empty `match` (catch-all)."
                if not rules_ok else ""
            ),
        })

    # 6. Channels (slack, etc.). Each registered channel contributes
    # one check via `is_ready()`. Empty registry adds zero checks --
    # an OSS install without any channel impls still passes setup.
    from . import channels as _channel_registry
    for ch in _channel_registry.all_channels():
        try:
            ok, detail = ch.is_ready()
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"is_ready raised: {e}"
        cid = getattr(ch, "id", ch.__class__.__name__)
        label = getattr(ch, "label", cid)
        checks.append({
            "id": f"channel_{cid}",
            "label": f"Channel: {label}",
            "ok": ok,
            "detail": detail,
            "hint": (
                f"See the {label} channel's docs for configuration. "
                "Typical setup: drop credentials into the path the "
                "channel expects, then add at least one watched entity."
                if not ok else ""
            ),
        })

    return {
        "all_ok": all(c["ok"] for c in checks),
        "checks": checks,
    }
