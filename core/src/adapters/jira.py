"""JIRA HTTP adapter (the only place in Eva that talks to JIRA).

Supports two auth flavours, picked per-instance:

  - 'basic': email + Atlassian API token. Standard JIRA Cloud auth
    (Atlassian's docs path).
  - 'bearer': Personal Access Token (PAT) header. Used by on-prem /
    server JIRA installations like the Apache Foundation one
    (`issues.example.org/jira`) where Basic-auth-with-API-token isn't
    available.

The REST path also flexes per-flavour: cloud's v3 search endpoint
returns 405 on Apache server, which still serves the v2 path. We pick
v2 for `bearer` and v3 for `basic` -- callers can override with the
`api_path` argument if needed.

Network is wrapped in `_http_get` so tests can inject a fake.
"""

from __future__ import annotations
import common

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


# REST search paths. Cloud (v3) returns ADF for descriptions; v2
# returns plain text -- callers should be defensive about both.
SEARCH_PATH_V2 = "/rest/api/2/search"
SEARCH_PATH_V3 = "/rest/api/3/search"
# Atlassian Cloud's replacement for the deprecated v3 GET search.
# Required since April 2025; takes a POST + JSON body + cursor-based
# pagination via `nextPageToken`.
SEARCH_PATH_V3_JQL = "/rest/api/3/search/jql"

# Auth flavours.
AUTH_BASIC = "basic"
AUTH_BEARER = "bearer"
VALID_AUTH_TYPES = {AUTH_BASIC, AUTH_BEARER}


def _basic_auth_header(email: str, token: str) -> str:
    raw = f"{email}:{token}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _bearer_auth_header(token: str) -> str:
    return f"Bearer {token}"


def _http_get(url: str, headers: dict, timeout: int = 15) -> dict:
    """Perform a JSON GET. Wrapped so tests can monkey-patch it."""
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    return json.loads(body)


def _build_auth_header(auth_type: str, *, email: str, token: str) -> str:
    if auth_type == AUTH_BEARER:
        if not token:
            raise ValueError("Bearer auth requires a token")
        return _bearer_auth_header(token)
    if auth_type == AUTH_BASIC:
        if not (email and token):
            raise ValueError("Basic auth requires email + token")
        return _basic_auth_header(email, token)
    raise ValueError(f"unknown JIRA auth_type: {auth_type!r}")


def _default_search_path(auth_type: str) -> str:
    # Bearer is conventional on Apache server-style installs (v2);
    # Basic is conventional on Atlassian Cloud (v3). Override-able
    # via `api_path` argument when an install bucks the trend.
    return SEARCH_PATH_V2 if auth_type == AUTH_BEARER else SEARCH_PATH_V3


def search_issues(
    *,
    base_url: str,
    auth_type: str = AUTH_BASIC,
    email: str = "",
    api_token: str,
    jql: str,
    max_results: int = 100,
    api_path: str | None = None,
    http_get: Callable[..., dict] | None = None,
    http_post: Callable[..., dict] | None = None,
) -> list[dict]:
    """Run a JQL search against the configured JIRA instance.

    Returns the raw `issues` list (each entry is a JIRA REST issue
    dict). Callers (`common.tickets.sync`) pull out the fields we care
    about and persist them to the cache table.

    Atlassian Cloud deprecated `GET /rest/api/3/search` (returns 410
    Gone since April 2025) in favour of `POST /rest/api/3/search/jql`
    with a JSON body. We auto-fall-back from the legacy GET endpoint
    to the new POST endpoint on 410 / 404 / 405 so existing users on
    Cloud keep working without a manual config change.

    `http_get` / `http_post` are injectable so tests verify call shape
    without ever hitting the wire."""
    if not base_url:
        raise ValueError("JIRA base_url is required")
    if auth_type not in VALID_AUTH_TYPES:
        raise ValueError(
            f"auth_type must be one of {sorted(VALID_AUTH_TYPES)}; "
            f"got {auth_type!r}"
        )
    auth_header = _build_auth_header(auth_type, email=email, token=api_token)
    base = base_url.rstrip("/")
    path = api_path or _default_search_path(auth_type)
    fields = (
        "summary,status,priority,issuetype,assignee,reporter,"
        "created,updated,project,description,labels,components,"
        "fixVersions,parent,resolution"
    )
    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
    }
    qs = urllib.parse.urlencode({
        "jql": jql, "maxResults": max_results, "fields": fields,
    })
    url = f"{base}{path}?{qs}"
    fn_get = http_get or _http_get
    fn_post = http_post or _http_post
    try:
        body = fn_get(url, headers)
    except urllib.error.HTTPError as e:
        # Atlassian Cloud deprecated `GET /rest/api/3/search` in
        # April 2025; the replacement is `POST /rest/api/3/search/jql`
        # with a JSON body + cursor-based pagination. Auto-fall-back
        # so existing installs Just Work.
        if e.code in (404, 405, 410) and path == SEARCH_PATH_V3:
            new_url = f"{base}{SEARCH_PATH_V3_JQL}"
            payload = {
                "jql": jql,
                "maxResults": max_results,
                "fields": fields.split(","),
            }
            try:
                body = fn_post(new_url, headers, payload)
            except urllib.error.HTTPError as e2:
                raise RuntimeError(
                    f"JIRA returned {e2.code}: {e2.reason}"
                ) from e2
            except urllib.error.URLError as e2:
                raise RuntimeError(
                    f"JIRA connection failed: {e2.reason}") from e2
        else:
            raise RuntimeError(
                f"JIRA returned {e.code}: {e.reason}"
            ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"JIRA connection failed: {e.reason}") from e
    return body.get("issues") or []


def issue_url(base_url: str, key: str) -> str:
    """Browse-URL convention for a JIRA issue. `/browse/<KEY>` works
    on Atlassian Cloud and on-prem JIRA equally."""
    return f"{base_url.rstrip('/')}/browse/{key}"


def fetch_issue(
    *,
    base_url: str,
    auth_type: str = AUTH_BASIC,
    email: str = "",
    api_token: str,
    key: str,
    http_get: Callable[..., dict] | None = None,
) -> dict | None:
    """Fetch a single issue by key. Returns the raw JIRA REST issue
    dict, or None when the issue isn't visible to the calling user
    (404). Used by the auto-track path to pull a key the user pasted
    into a PR title / task field that we haven't synced yet.

    Same field-set as `search_issues` so the caller can call
    `_normalise_issue` on the result."""
    if not key:
        raise ValueError("issue key is required")
    auth_header = _build_auth_header(auth_type, email=email, token=api_token)
    base = base_url.rstrip("/")
    qs = urllib.parse.urlencode({
        "fields": "summary,status,priority,issuetype,assignee,reporter,"
                  "created,updated,project,description,labels,components,"
                  "fixVersions,parent,resolution",
    })
    url = f"{base}/rest/api/2/issue/{key}?{qs}"
    headers = {
        "Authorization": auth_header,
        "Accept": "application/json",
    }
    fn = http_get or _http_get
    try:
        body = fn(url, headers)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise RuntimeError(f"JIRA returned {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"JIRA connection failed: {e.reason}") from e
    return body or None


# ---- Write-side helpers (Phase 2) -----------------------------------
#
# Comment / transition / list-transitions are POST/GET endpoints that
# write to JIRA. They share the same auth + http_post/get plumbing as
# `search_issues`. Each is wrapped so a 4xx/5xx surfaces as a
# RuntimeError with the JIRA-side reason intact -- the route layer
# maps it to a 502 the frontend can show.


def _http_post(url: str, headers: dict, payload: dict | None = None,
               timeout: int = 15) -> dict:
    """POST helper. Empty payload is allowed (some transition endpoints
    accept an empty body). Returns the parsed JSON response, or `{}`
    when the server returns 204 No Content."""
    body_bytes = b""
    if payload is not None:
        body_bytes = json.dumps(payload).encode("utf-8")
    full_headers = dict(headers)
    full_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(
        url, data=body_bytes, headers=full_headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Some on-prem servers return text/plain "OK" on 200; tolerate.
        return {"raw": raw.decode("utf-8", errors="replace")}


def _instance_headers(auth_type: str, *, email: str, token: str) -> dict:
    """Auth + Accept headers shared by every write call."""
    return {
        "Authorization": _build_auth_header(auth_type, email=email, token=token),
        "Accept": "application/json",
    }


def add_comment(
    *,
    base_url: str,
    auth_type: str = AUTH_BASIC,
    email: str = "",
    api_token: str,
    key: str,
    body: str,
    http_post: Callable[..., dict] | None = None,
) -> dict:
    """Post a plain-text comment on `<KEY>`. Server (v2) accepts a
    plain `{"body": "..."}` payload; Cloud (v3) wants ADF, but Cloud
    also still accepts the v2 path for back-compat -- we use v2
    consistently to keep the body shape simple. Tests can inject
    `http_post`.
    """
    if not body:
        raise ValueError("comment body is required")
    if not key:
        raise ValueError("issue key is required")
    headers = _instance_headers(auth_type, email=email, token=api_token)
    base = base_url.rstrip("/")
    url = f"{base}/rest/api/2/issue/{key}/comment"
    payload = {"body": body}
    fn = http_post or _http_post
    try:
        return fn(url, headers, payload=payload)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"JIRA returned {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"JIRA connection failed: {e.reason}") from e


def list_transitions(
    *,
    base_url: str,
    auth_type: str = AUTH_BASIC,
    email: str = "",
    api_token: str,
    key: str,
    http_get: Callable[..., dict] | None = None,
) -> list[dict]:
    """Fetch the transitions available *to the calling user* for the
    given issue. Each entry has at least `id` and `name` (e.g.
    `{"id": "21", "name": "Resolved"}`) -- the UI shows `name` and
    posts back `id` to apply the transition."""
    if not key:
        raise ValueError("issue key is required")
    headers = _instance_headers(auth_type, email=email, token=api_token)
    base = base_url.rstrip("/")
    url = f"{base}/rest/api/2/issue/{key}/transitions"
    fn = http_get or _http_get
    try:
        body = fn(url, headers)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"JIRA returned {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"JIRA connection failed: {e.reason}") from e
    return body.get("transitions") or []


def transition_issue(
    *,
    base_url: str,
    auth_type: str = AUTH_BASIC,
    email: str = "",
    api_token: str,
    key: str,
    transition_id: str,
    resolution: str = "",
    comment: str = "",
    http_post: Callable[..., dict] | None = None,
) -> dict:
    """Apply a transition (e.g. "Resolve", "Close") to the issue.

    `transition_id` is the JIRA-side id from `list_transitions`. The
    optional `resolution` lets the caller set the resolution field as
    part of the transition (JIRA Cloud requires this on Resolve);
    `comment` attaches a one-shot comment with the same payload --
    saves a round-trip vs `add_comment` followed by `transition`.
    """
    if not key:
        raise ValueError("issue key is required")
    if not transition_id:
        raise ValueError("transition_id is required")
    headers = _instance_headers(auth_type, email=email, token=api_token)
    base = base_url.rstrip("/")
    url = f"{base}/rest/api/2/issue/{key}/transitions"
    payload: dict = {"transition": {"id": str(transition_id)}}
    if resolution:
        payload["fields"] = {"resolution": {"name": resolution}}
    if comment:
        payload["update"] = {"comment": [{"add": {"body": comment}}]}
    fn = http_post or _http_post
    try:
        return fn(url, headers, payload=payload)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"JIRA returned {e.code}: {e.reason}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"JIRA connection failed: {e.reason}") from e
