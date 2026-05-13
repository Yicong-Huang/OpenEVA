"""Tests for tickets + routes.tickets + adapters.jira.

Network is never hit -- `adapters.jira._http_get` is replaced with an
in-memory fake that returns canned issue payloads.
"""

import common
import pytest

from adapters import jira as jira_adapter
from common import tickets as core_tickets
from common import settings as core_settings


# ---- Fake JIRA payloads (small but representative) ----

def _fake_issue(key="ABC-1", summary="A bug", status="In Progress",
                assignee="dev@example.com", project="ABC",
                priority="Medium", issue_type="Bug",
                created="2026-04-25T08:00:00Z",
                updated="2026-04-25T09:00:00Z") -> dict:
    """Mirror the JIRA REST issue shape just enough for the
    normaliser to find every field it cares about."""
    return {
        "key": key,
        "fields": {
            "summary": summary,
            "description": f"description for {key}",
            "status": {"name": status},
            "priority": {"name": priority},
            "issuetype": {"name": issue_type},
            "project": {"key": project},
            "assignee": {"emailAddress": assignee},
            "reporter": {"emailAddress": "pm@example.com"},
            "created": created,
            "updated": updated,
        },
    }


def _configure_jira(db, email="dev@example.com",
                    name="default",
                    base_url="https://example.atlassian.net",
                    auth_type="basic",
                    replace=True):
    """Configure a JIRA instance for tests.

    By default writes the legacy single-instance settings keys, which
    `_migrate_legacy_jira_singleton` lifts into a `default` instance
    on first sync. When `name` != 'default' we write directly into
    the multi-instance list under `service.jira.instances` so callers
    can register multiple instances. `replace=True` (default) wipes
    any pre-existing instance list; pass `replace=False` to append.
    """
    if name == "default":
        db.set_setting(core_settings.KEY_JIRA_BASE_URL, base_url)
        db.set_setting(core_settings.KEY_JIRA_EMAIL, email)
        db.set_setting(core_settings.KEY_JIRA_API_TOKEN, "FAKE_TOKEN")
    entry = {
        "name": name,
        "base_url": base_url,
        "auth_type": auth_type,
        "email": email,
        "api_token": "FAKE_TOKEN",
        "jql": "assignee = currentUser()",
    }
    existing = db.get_setting(core_settings.KEY_JIRA_INSTANCES) or []
    if not isinstance(existing, list) or replace:
        existing = []
    existing = [i for i in existing
                if isinstance(i, dict) and i.get("name") != name]
    existing.append(entry)
    db.set_setting(core_settings.KEY_JIRA_INSTANCES, existing)


# ---- adapters.jira ----

class TestJiraAdapter:
    def test_search_issues_builds_correct_url_and_headers(self):
        captured = {}
        def fake_get(url, headers, timeout=15):
            captured["url"] = url
            captured["headers"] = headers
            return {"issues": []}
        jira_adapter.search_issues(
            base_url="https://example.atlassian.net",
            email="dev@example.com",
            api_token="TOKEN",
            jql="assignee = currentUser()",
            http_get=fake_get,
        )
        assert captured["url"].startswith(
            "https://example.atlassian.net/rest/api/3/search"
        )
        # JQL is URL-encoded into the query string.
        assert "jql=assignee" in captured["url"]
        # Basic auth header is well-formed (base64 of email:token).
        assert captured["headers"]["Authorization"].startswith("Basic ")
        assert captured["headers"]["Accept"] == "application/json"

    def test_search_issues_strips_trailing_slash_in_base_url(self):
        captured = {}
        def fake_get(url, headers, timeout=15):
            captured["url"] = url
            return {"issues": []}
        jira_adapter.search_issues(
            base_url="https://example.atlassian.net/",
            email="x", api_token="t", jql="*",
            http_get=fake_get,
        )
        assert "//rest/" not in captured["url"]

    def test_search_issues_rejects_missing_creds(self):
        with pytest.raises(ValueError, match="required"):
            jira_adapter.search_issues(
                base_url="", email="x", api_token="t", jql="*",
            )

    def test_search_issues_returns_issues_list(self):
        def fake_get(url, headers, timeout=15):
            return {"issues": [_fake_issue("ONE-1"), _fake_issue("ONE-2")]}
        out = jira_adapter.search_issues(
            base_url="https://j.example",
            email="d@e.com", api_token="T",
            jql="*", http_get=fake_get,
        )
        assert len(out) == 2
        assert out[0]["key"] == "ONE-1"

    def test_search_issues_falls_back_to_v3_jql_on_410_gone(self):
        """Atlassian Cloud deprecated `GET /rest/api/3/search` in
        April 2025 -> 410 Gone. The adapter must auto-retry against
        `POST /rest/api/3/search/jql` so existing Cloud installs
        keep working."""
        import urllib.error
        get_calls = []
        post_calls = []

        def fake_get(url, headers, timeout=15):
            get_calls.append(url)
            raise urllib.error.HTTPError(
                url, 410, "Gone", {}, None,
            )

        def fake_post(url, headers, payload, timeout=15):
            post_calls.append({"url": url, "payload": payload})
            return {"issues": [_fake_issue("FALL-1")]}

        out = jira_adapter.search_issues(
            base_url="https://j.example",
            email="d@e.com", api_token="T",
            jql="assignee = me", http_get=fake_get, http_post=fake_post,
        )
        # Got back the issues from the fallback POST.
        assert len(out) == 1
        assert out[0]["key"] == "FALL-1"
        # GET was tried once at the legacy v3 path.
        assert len(get_calls) == 1
        assert "/rest/api/3/search?" in get_calls[0]
        # POST hit the new v3-jql path with a JSON body containing the
        # JQL + fields list.
        assert len(post_calls) == 1
        assert post_calls[0]["url"].endswith("/rest/api/3/search/jql")
        body = post_calls[0]["payload"]
        assert body["jql"] == "assignee = me"
        assert "summary" in body["fields"]

    def test_search_issues_does_not_fall_back_on_500(self):
        """500/503 errors must NOT trigger the v3-jql fallback -- those
        are server-side problems, not deprecations. The adapter raises
        a RuntimeError so the caller can surface it."""
        import urllib.error
        import pytest
        post_called = []

        def fake_get(url, headers, timeout=15):
            raise urllib.error.HTTPError(
                url, 500, "Internal Server Error", {}, None,
            )

        def fake_post(url, headers, payload, timeout=15):
            post_called.append(url)
            return {"issues": []}

        with pytest.raises(RuntimeError, match=r"500"):
            jira_adapter.search_issues(
                base_url="https://j.example",
                email="d@e.com", api_token="T",
                jql="*", http_get=fake_get, http_post=fake_post,
            )
        assert post_called == []

    def test_issue_url_format(self):
        assert jira_adapter.issue_url("https://j.example", "ABC-1") \
            == "https://j.example/browse/ABC-1"
        assert jira_adapter.issue_url("https://j.example/", "ABC-1") \
            == "https://j.example/browse/ABC-1"


class TestJiraAuthHelpers:
    """Unit-test the auth-header builders + the validation arms that
    routes/core never directly exercise (callers always supply
    valid creds since core/tickets validates upstream). Without
    these, a future regression that loosens the validation could
    leak unauthenticated requests."""

    def test_basic_auth_header_format(self):
        header = jira_adapter._basic_auth_header("alice@example.com", "tok")
        assert header.startswith("Basic ")
        # Decode + verify the body looks like email:token base64-encoded.
        import base64
        encoded = header.split(" ", 1)[1]
        assert base64.b64decode(encoded).decode("utf-8") == \
            "alice@example.com:tok"

    def test_bearer_auth_header_format(self):
        assert jira_adapter._bearer_auth_header("tok") == "Bearer tok"

    def test_build_auth_header_basic_requires_email_and_token(self):
        import pytest
        with pytest.raises(ValueError, match="Basic auth requires"):
            jira_adapter._build_auth_header(
                jira_adapter.AUTH_BASIC, email="", token="tok",
            )
        with pytest.raises(ValueError, match="Basic auth requires"):
            jira_adapter._build_auth_header(
                jira_adapter.AUTH_BASIC, email="x@x", token="",
            )

    def test_build_auth_header_bearer_requires_token(self):
        import pytest
        with pytest.raises(ValueError, match="Bearer auth requires"):
            jira_adapter._build_auth_header(
                jira_adapter.AUTH_BEARER, email="", token="",
            )

    def test_build_auth_header_rejects_unknown_auth_type(self):
        import pytest
        with pytest.raises(ValueError, match="unknown JIRA auth_type"):
            jira_adapter._build_auth_header(
                "kerberos", email="x@x", token="t",
            )

    def test_default_search_path_picks_v2_for_bearer(self):
        # JIRA Server-style installs use v2; Atlassian Cloud uses v3.
        # The picker is what makes the same `search_issues` call work
        # against both without per-call api_path overrides.
        assert jira_adapter._default_search_path(jira_adapter.AUTH_BEARER) \
            == jira_adapter.SEARCH_PATH_V2
        assert jira_adapter._default_search_path(jira_adapter.AUTH_BASIC) \
            == jira_adapter.SEARCH_PATH_V3


class TestJiraAdapterErrorMappings:
    """The HTTP/URL error -> RuntimeError mapping in every adapter
    function. Routes layer maps RuntimeError -> 502 for the user;
    if the adapter swallows or mistypes the error, the user gets a
    confusing 500 instead. Each test injects a fake `http_get` /
    `http_post` that raises the relevant `urllib.error.*` exception."""

    def _http_error(self, code, msg):
        import urllib.error
        return urllib.error.HTTPError(
            url="x", code=code, msg=msg, hdrs=None,
            fp=None,  # type: ignore[arg-type]
        )

    def _url_error(self, reason):
        import urllib.error
        return urllib.error.URLError(reason)

    def _common_kwargs(self):
        return dict(
            base_url="https://x.example",
            auth_type="basic",
            email="a@x.example",
            api_token="T",
        )

    def test_search_issues_maps_http_error_to_runtime(self):
        import pytest
        def fake_get(*a, **kw):
            raise self._http_error(403, "Forbidden")
        with pytest.raises(RuntimeError, match="403"):
            jira_adapter.search_issues(
                **self._common_kwargs(),
                jql="project=X", http_get=fake_get,
            )

    def test_search_issues_maps_url_error_to_runtime(self):
        import pytest
        def fake_get(*a, **kw):
            raise self._url_error("connection refused")
        with pytest.raises(RuntimeError, match="connection refused"):
            jira_adapter.search_issues(
                **self._common_kwargs(),
                jql="project=X", http_get=fake_get,
            )

    def test_fetch_issue_returns_none_on_404(self):
        # 404 specifically should NOT raise -- callers (track) use
        # None to mean "not visible to this user; try the next instance".
        def fake_get(*a, **kw):
            raise self._http_error(404, "Not Found")
        out = jira_adapter.fetch_issue(
            **self._common_kwargs(),
            key="GONE-1", http_get=fake_get,
        )
        assert out is None

    def test_fetch_issue_maps_non_404_http_error_to_runtime(self):
        import pytest
        def fake_get(*a, **kw):
            raise self._http_error(403, "Forbidden")
        with pytest.raises(RuntimeError, match="403"):
            jira_adapter.fetch_issue(
                **self._common_kwargs(),
                key="X-1", http_get=fake_get,
            )

    def test_fetch_issue_requires_key(self):
        import pytest
        with pytest.raises(ValueError, match="issue key"):
            jira_adapter.fetch_issue(
                **self._common_kwargs(), key="",
            )

    def test_add_comment_requires_body_and_key(self):
        import pytest
        with pytest.raises(ValueError, match="comment body"):
            jira_adapter.add_comment(
                **self._common_kwargs(), key="X-1", body="",
            )
        with pytest.raises(ValueError, match="issue key"):
            jira_adapter.add_comment(
                **self._common_kwargs(), key="", body="hi",
            )

    def test_add_comment_maps_http_error(self):
        import pytest
        def fake_post(*a, **kw):
            raise self._http_error(401, "Unauthorized")
        with pytest.raises(RuntimeError, match="401"):
            jira_adapter.add_comment(
                **self._common_kwargs(), key="X-1", body="hi",
                http_post=fake_post,
            )

    def test_add_comment_maps_url_error(self):
        import pytest
        def fake_post(*a, **kw):
            raise self._url_error("timeout")
        with pytest.raises(RuntimeError, match="timeout"):
            jira_adapter.add_comment(
                **self._common_kwargs(), key="X-1", body="hi",
                http_post=fake_post,
            )

    def test_list_transitions_requires_key(self):
        import pytest
        with pytest.raises(ValueError, match="issue key"):
            jira_adapter.list_transitions(
                **self._common_kwargs(), key="",
            )

    def test_list_transitions_maps_http_error(self):
        import pytest
        def fake_get(*a, **kw):
            raise self._http_error(500, "Server Error")
        with pytest.raises(RuntimeError, match="500"):
            jira_adapter.list_transitions(
                **self._common_kwargs(), key="X-1",
                http_get=fake_get,
            )

    def test_list_transitions_maps_url_error(self):
        import pytest
        def fake_get(*a, **kw):
            raise self._url_error("dns failure")
        with pytest.raises(RuntimeError, match="dns failure"):
            jira_adapter.list_transitions(
                **self._common_kwargs(), key="X-1",
                http_get=fake_get,
            )

    def test_transition_issue_requires_key_and_id(self):
        import pytest
        with pytest.raises(ValueError, match="issue key"):
            jira_adapter.transition_issue(
                **self._common_kwargs(), key="", transition_id="21",
            )
        with pytest.raises(ValueError, match="transition_id"):
            jira_adapter.transition_issue(
                **self._common_kwargs(), key="X-1", transition_id="",
            )

    def test_transition_issue_maps_http_error(self):
        import pytest
        def fake_post(*a, **kw):
            raise self._http_error(403, "Forbidden")
        with pytest.raises(RuntimeError, match="403"):
            jira_adapter.transition_issue(
                **self._common_kwargs(), key="X-1",
                transition_id="21", http_post=fake_post,
            )

    def test_transition_issue_maps_url_error(self):
        import pytest
        def fake_post(*a, **kw):
            raise self._url_error("network down")
        with pytest.raises(RuntimeError, match="network down"):
            jira_adapter.transition_issue(
                **self._common_kwargs(), key="X-1",
                transition_id="21", http_post=fake_post,
            )

    def test_http_post_handles_no_content_response(self):
        # `_http_post` returns {} on empty body; covers the
        # 204-No-Content branch that JIRA Cloud returns on Resolve.
        import io
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = b""
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("adapters.jira.urllib.request.urlopen",
                   return_value=mock_resp):
            out = jira_adapter._http_post(
                "https://x.example/x", {"Authorization": "Basic xxx"},
            )
        assert out == {}

    def test_http_post_handles_text_plain_ok_response(self):
        # Some on-prem servers return text/plain "OK" on 200.
        # Adapter wraps it as {"raw": "..."} so callers don't crash
        # on a JSONDecodeError.
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"OK"
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("adapters.jira.urllib.request.urlopen",
                   return_value=mock_resp):
            out = jira_adapter._http_post(
                "https://x.example/x", {"Authorization": "Basic xxx"},
            )
        assert out == {"raw": "OK"}

    def test_http_post_serialises_payload_when_provided(self):
        # _http_post with a payload (not None) must encode it as JSON.
        # The 'on transition' route passes a payload; coverage of the
        # encoding branch (line 197) avoids regressing the wire format.
        import json as _json
        from unittest.mock import patch, MagicMock
        captured = {}
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": 1}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        def capture_urlopen(req, timeout=15):
            captured["data"] = req.data
            captured["method"] = req.get_method()
            return mock_resp

        with patch("adapters.jira.urllib.request.urlopen",
                   side_effect=capture_urlopen):
            out = jira_adapter._http_post(
                "https://x.example/x",
                {"Authorization": "Basic xxx"},
                payload={"transition": {"id": "21"}},
            )
        assert out == {"ok": 1}
        assert captured["method"] == "POST"
        assert _json.loads(captured["data"]) == {"transition": {"id": "21"}}

    def test_http_get_round_trips_through_urlopen(self):
        # _http_get is normally mocked at the call-site; cover the body
        # itself by mocking only urllib.request.urlopen so the real
        # adapter code (Request build + json.loads) executes.
        from unittest.mock import patch, MagicMock
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"hello": "world"}'
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("adapters.jira.urllib.request.urlopen",
                   return_value=mock_resp):
            out = jira_adapter._http_get(
                "https://x.example/x", {"Accept": "application/json"},
            )
        assert out == {"hello": "world"}

    def test_build_auth_header_bearer_returns_bearer_prefix(self):
        # Positive path through _build_auth_header for bearer auth.
        # Without this the `return _bearer_auth_header(token)` branch
        # is uncovered (only the 'no token' raise was tested).
        out = jira_adapter._build_auth_header(
            jira_adapter.AUTH_BEARER, email="", token="pat_abc",
        )
        assert out == "Bearer pat_abc"

    def test_search_issues_rejects_unknown_auth_type(self):
        # search_issues has its own auth_type guard (line 98-102) that
        # fires before _build_auth_header even runs. A bogus value
        # must surface as a clean ValueError, not a 500.
        import pytest
        with pytest.raises(ValueError, match="auth_type must be"):
            jira_adapter.search_issues(
                base_url="https://x.example",
                auth_type="kerberos",
                email="a@x.example",
                api_token="T",
                jql="project=X",
            )

    def test_fetch_issue_maps_url_error_to_runtime(self):
        # Network-down path of fetch_issue (URLError, distinct from the
        # HTTPError 4xx path). Must surface as RuntimeError so the
        # route layer maps it to a 502 rather than leaking the urllib
        # exception type to the client.
        import pytest
        def fake_get(*a, **kw):
            raise self._url_error("connection refused")
        with pytest.raises(RuntimeError, match="connection refused"):
            jira_adapter.fetch_issue(
                **self._common_kwargs(),
                key="X-1", http_get=fake_get,
            )


# ---- tickets ----

class TestIsConfigured:
    def test_false_when_empty(self, patched_server):
        assert core_tickets.is_configured() is False

    def test_true_when_all_set(self, patched_server):
        _configure_jira(patched_server._db)
        assert core_tickets.is_configured() is True

    def test_false_when_partial(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_JIRA_BASE_URL, "https://j.example")
        # email + token still missing.
        assert core_tickets.is_configured() is False


class TestNormaliseIssue:
    _INST = {
        "name": "test", "base_url": "https://j.example",
        "auth_type": "basic", "email": "x", "api_token": "t",
        "jql": "*",
    }

    def test_extracts_canonical_fields(self):
        issue = _fake_issue("ABC-7", summary="hello")
        out = core_tickets._normalise_issue(issue, self._INST)
        assert out["key"] == "ABC-7"
        assert out["summary"] == "hello"
        assert out["status"] == "In Progress"
        assert out["assignee_email"] == "dev@example.com"
        assert out["url"] == "https://j.example/browse/ABC-7"
        assert out["project_key"] == "ABC"
        # Multi-instance: the instance name is recorded on the row.
        assert out["instance_name"] == "test"

    def test_handles_missing_nested_fields(self):
        # Defensive: assignee/reporter/etc. can be null in JIRA.
        issue = {
            "key": "X-1",
            "fields": {"summary": "minimal"},
        }
        out = core_tickets._normalise_issue(issue, self._INST)
        assert out["key"] == "X-1"
        assert out["summary"] == "minimal"
        assert out["assignee_email"] == ""
        assert out["status"] == ""


class TestSync:
    def test_sync_inserts_issues_into_cache(self, patched_server):
        _configure_jira(patched_server._db)
        def fake_get(url, headers, timeout=15):
            return {"issues": [
                _fake_issue("PROJ-1", summary="first"),
                _fake_issue("PROJ-2", summary="second"),
            ]}
        out = core_tickets.sync(http_get=fake_get)
        assert out["count"] == 2
        # Both rows ended up in the cache.
        cached = core_tickets.list_tickets()
        keys = sorted(t["key"] for t in cached)
        assert keys == ["PROJ-1", "PROJ-2"]

    def test_sync_prunes_tickets_no_longer_in_jql(self, patched_server):
        _configure_jira(patched_server._db)
        # Round 1: two tickets returned.
        def round1(url, headers, timeout=15):
            return {"issues": [_fake_issue("A-1"), _fake_issue("A-2")]}
        core_tickets.sync(http_get=round1)
        assert len(core_tickets.list_tickets()) == 2
        # Round 2: only A-1 returned -- A-2 should be pruned.
        def round2(url, headers, timeout=15):
            return {"issues": [_fake_issue("A-1")]}
        out = core_tickets.sync(http_get=round2)
        assert out["count"] == 1
        assert out["pruned"] >= 1
        keys = [t["key"] for t in core_tickets.list_tickets()]
        assert "A-1" in keys
        assert "A-2" not in keys

    def test_sync_does_not_prune_other_users_tickets(self, patched_server):
        # A coworker's cached ticket must survive my sync.
        patched_server._db.upsert_ticket(
            key="OTHER-1", summary="not mine",
            assignee_email="coworker@example.com", synced_at="2026-01-01T00:00:00",
        )
        _configure_jira(patched_server._db, email="dev@example.com")
        def fake_get(url, headers, timeout=15):
            return {"issues": [_fake_issue("MINE-1")]}
        core_tickets.sync(http_get=fake_get)
        # Coworker's ticket still in the cache (different assignee_email).
        keys = [t["key"] for t in patched_server._db.list_tickets()]
        assert "OTHER-1" in keys

    def test_sync_raises_when_not_configured(self, patched_server):
        # Multi-instance: error message updated; still ValueError.
        with pytest.raises(ValueError, match="No JIRA instances"):
            core_tickets.sync()

    def test_sync_skips_malformed_issues(self, patched_server):
        _configure_jira(patched_server._db)
        def fake_get(url, headers, timeout=15):
            return {"issues": [
                _fake_issue("OK-1"),
                {"fields": {"summary": "no key!"}},  # missing key
            ]}
        out = core_tickets.sync(http_get=fake_get)
        # OK-1 indexed, malformed silently skipped.
        assert out["count"] == 1


class TestListTickets:
    def test_empty_when_no_cache(self, patched_server):
        assert core_tickets.list_tickets() == []

    def test_returns_all_cached_when_configured(self, patched_server):
        # Multi-instance: per-user filtering happens on the JIRA side
        # (each instance carries its own JQL). Eva returns whatever
        # is cached for any configured instance.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="A-1",
            assignee_email="me@example.com",
        )
        patched_server._db.upsert_ticket(
            instance_name="primary", key="A-2",
            assignee_email="other@example.com",
        )
        _configure_jira(patched_server._db, email="me@example.com")
        out = core_tickets.list_tickets()
        keys = [t["key"] for t in out]
        assert sorted(keys) == ["A-1", "A-2"]

    def test_returns_empty_when_no_instances_configured(self, patched_server):
        # Even if cache has rows, no configured instance -> empty
        # list (avoids leaking stale rows after the user removes
        # all instances).
        patched_server._db.upsert_ticket(
            instance_name="orphan", key="ORPHAN-1",
            assignee_email="x@example.com",
        )
        out = core_tickets.list_tickets()
        assert out == []


class TestEnrichForView:
    """Phase-1 enrichment: parse JSON list columns + derive category +
    reverse-link to tasks."""

    def test_parses_json_list_columns(self, patched_server):
        import json
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ENR-1",
            labels=json.dumps(["flaky-test", "ci"]),
            components=json.dumps(["Example"]),
            fix_versions=json.dumps(["4.2.0"]),
        )
        row = patched_server._db.get_ticket("ENR-1", instance_name="primary")
        out = core_tickets.enrich_for_view(row)
        assert out["labels"] == ["flaky-test", "ci"]
        assert out["components"] == ["Example"]
        assert out["fix_versions"] == ["4.2.0"]

    def test_blank_columns_default_to_empty_lists(self, patched_server):
        # Brand-new row with no enrichment yet (e.g. legacy sync) ->
        # all list columns default to [] so the UI doesn't have to
        # nullcheck.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ENR-2",
        )
        row = patched_server._db.get_ticket("ENR-2", instance_name="primary")
        out = core_tickets.enrich_for_view(row)
        assert out["labels"] == []
        assert out["components"] == []
        assert out["fix_versions"] == []

    def test_malformed_json_falls_back_to_empty(self, patched_server):
        # A bad JSON blob can land if the schema's queried directly;
        # the enricher must not crash.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ENR-3",
            labels="not-actually-json",
        )
        row = patched_server._db.get_ticket("ENR-3", instance_name="primary")
        out = core_tickets.enrich_for_view(row)
        assert out["labels"] == []

    def test_category_prefix_from_key(self, patched_server):
        patched_server._db.upsert_ticket(
            instance_name="primary", key="INTKEY-42",
        )
        row = patched_server._db.get_ticket("INTKEY-42", instance_name="primary")
        assert core_tickets.enrich_for_view(row)["category"] == "INTKEY"

    def test_category_blank_when_key_has_no_dash(self, patched_server):
        patched_server._db.upsert_ticket(
            instance_name="primary", key="NODASH",
        )
        row = patched_server._db.get_ticket("NODASH", instance_name="primary")
        assert core_tickets.enrich_for_view(row)["category"] == ""

    def test_reverse_links_to_tasks_with_matching_ticket_id(
        self, patched_server,
    ):
        # task-c in conftest has ticket_id=EX-123. Cache that key
        # and verify enrich_for_view picks it up.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="EX-123",
        )
        row = patched_server._db.get_ticket("EX-123", instance_name="primary")
        out = core_tickets.enrich_for_view(row)
        assert out["linked_tasks"]
        # conftest seeds task-c with this ticket.
        keys = [(lt["project"], lt["task_id"]) for lt in out["linked_tasks"]]
        assert ("test-proj", "task-c") in keys

    def test_no_linked_tasks_when_key_unreferenced(self, patched_server):
        patched_server._db.upsert_ticket(
            instance_name="primary", key="OBSCURE-1",
        )
        row = patched_server._db.get_ticket("OBSCURE-1", instance_name="primary")
        assert core_tickets.enrich_for_view(row)["linked_tasks"] == []


class TestGetOne:
    def test_returns_enriched_row(self, patched_server):
        _configure_jira(patched_server._db, email="me@example.com")
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ONE-1",
        )
        out = core_tickets.get_one("ONE-1", instance_name="primary")
        assert out is not None
        assert out["key"] == "ONE-1"
        # Enrichment fields are present.
        assert "labels" in out
        assert "category" in out
        assert "linked_tasks" in out

    def test_returns_none_for_missing_key(self, patched_server):
        assert core_tickets.get_one("NOPE", instance_name="primary") is None

    def test_get_route_404s_for_missing(self, client):
        r = client.get("/api/tickets/MISSING-1")
        assert r.status_code == 404

    def test_get_route_returns_cached_row_when_present(self, client):
        # Hit the success branch of `get_ticket` -- a cached row is
        # returned with enrichment fields. Without this the
        # `return out` line of the route is uncovered.
        from server import _db
        _db.upsert_ticket(
            instance_name="primary", key="HIT-1",
            summary="exists", status="Open",
        )
        r = client.get("/api/tickets/HIT-1?instance_name=primary")
        assert r.status_code == 200
        body = r.json()
        assert body["key"] == "HIT-1"
        assert body["summary"] == "exists"
        # Enrichment is applied.
        assert "labels" in body
        assert "linked_tasks" in body


class TestTicketsRouteFreshOSS:
    """Memo item #8: an open-source user clones Eva, doesn't configure
    JIRA, and visits the Tickets page. Every `/api/tickets*` endpoint
    must respond cleanly -- no 500s, no leaking stale cached rows
    from a different user's data.
    """

    def test_GET_tickets_returns_unconfigured_envelope(self, client):
        """The Tickets page reads `configured` to decide between
        "configure JIRA" CTA vs "no tickets match your JQL". Lock the
        envelope shape so a future route refactor doesn't accidentally
        flip the boolean default to True."""
        r = client.get("/api/tickets")
        assert r.status_code == 200
        body = r.json()
        assert body == {
            "tickets": [],
            "configured": False,
            "instances": [],
        }

    def test_GET_tickets_with_limit_param_works_when_unconfigured(self, client):
        """The `limit` query-param is honoured but moot when there's
        nothing to list -- just verifies it doesn't blow up."""
        r = client.get("/api/tickets?limit=5")
        assert r.status_code == 200
        assert r.json()["tickets"] == []

    def test_GET_tickets_clamps_above_max_limit(self, client):
        """`?limit=999999` is clamped to 1000 -- a hostile / typoed
        caller can no longer ask the DB for an unbounded scan."""
        r = client.get("/api/tickets?limit=999999")
        assert r.status_code == 200
        # Empty cache so output is empty, but the route must not 500.
        assert isinstance(r.json()["tickets"], list)

    def test_GET_tickets_clamps_below_min_limit(self, client):
        """`?limit=0` clamps to 1 -- the route returns at most 1 row
        rather than degenerate-empty."""
        r = client.get("/api/tickets?limit=0")
        assert r.status_code == 200
        assert isinstance(r.json()["tickets"], list)

    def test_GET_ticket_detail_404s_when_unconfigured(self, client):
        """Ticket-detail route should 404 cleanly when the requested
        key isn't cached (which is always true when no instance is
        configured), not 500 on a config lookup failure."""
        r = client.get("/api/tickets/JIRA-1")
        assert r.status_code == 404

    def test_POST_sync_422s_with_helpful_message_when_unconfigured(self, client):
        """Sync endpoint should reject with a 422 + actionable error
        when no instances exist (rather than 200 with an empty summary
        the user might mistake for "everything is fine"). Contract:
        - status_code == 422 (validation-style client error)
        - detail mentions "JIRA instances" + "Settings"
        so the frontend can surface the message verbatim instead of
        crafting its own copy.
        """
        r = client.post("/api/tickets/sync")
        assert r.status_code == 422, r.text
        body = r.json()
        assert "JIRA" in body["detail"], body
        assert "Settings" in body["detail"], body

    def test_POST_sync_named_instance_404s_when_not_configured(self, client):
        """Sync-by-name should 404 (or 422) for an unknown instance,
        matching the error contract for instance-by-name endpoints."""
        r = client.post("/api/tickets/sync/never_configured")
        assert r.status_code in (404, 422), r.text


class TestTriage:
    """`common.tickets.triage()` + GET /api/tickets/{key}/triage. Phase-1
    pulls JIRA fields together so the user can scan a flaky-test
    ticket without clicking through the JIRA page. Phase-2 (git-blame)
    is pending; today the `blame` field is None."""

    def _seed_flaky_ticket(self, db):
        # Seed ONE flaky-test ticket with a description that mentions
        # a few file paths so `_extract_file_paths` has work to do.
        db.upsert_ticket(
            instance_name="primary",
            key="EX-1002",
            summary="Test foo.bar.BazSuite is flaky on master",
            description=(
                "Stack trace:\n"
                "  at src/Foo.py:12\n"
                "  at python/example/sql/connect/client.py:45\n"
                "Last green: abcdef. Investigate."
            ),
            status="Open",
            priority="Major",
            issue_type="Bug",
            assignee_email="alice@example.com",
            reporter_email="bob@example.com",
            project_key="ES",
            components='["SQL Core"]',
            labels='["flaky", "needs-triage"]',
        )
        _configure_jira(db, name="primary", email="me@example.com")

    def test_triage_returns_structured_report(self, patched_server):
        self._seed_flaky_ticket(patched_server._db)
        out = core_tickets.triage("EX-1002", instance_name="primary")
        assert out is not None
        # Ticket envelope.
        assert out["ticket"]["key"] == "EX-1002"
        assert out["ticket"]["status"] == "Open"
        assert out["ticket"]["priority"] == "Major"
        # `url` is built at ingest time by `_normalise_issue`; the
        # raw `upsert_ticket` seed may leave it empty. Just check the
        # key is present in the response shape.
        assert "url" in out["ticket"]
        # Owner block: assignee + reporter + components + labels.
        assert out["owner"]["assignee"] == "alice@example.com"
        assert out["owner"]["reporter"] == "bob@example.com"
        assert "SQL Core" in out["owner"]["components"]
        assert "flaky" in out["owner"]["labels"]
        assert out["owner"]["project_key"] == "ES"
        # Files referenced: extracted from description.
        files = out["files_referenced"]
        assert "src/Foo.py" in files
        assert "python/example/sql/connect/client.py" in files
        # Phase-2: blame is now an empty list when no local repos are
        # configured (was None as a Phase-1 placeholder); the empty
        # list is the canonical "no blame found" shape.
        assert out["blame"] == []

    def test_triage_returns_none_when_ticket_not_cached(self, patched_server):
        # No seed -> ticket isn't in the cache.
        _configure_jira(patched_server._db)
        assert core_tickets.triage("NONE-1") is None

    def test_triage_route_404s_when_not_cached(self, client):
        r = client.get("/api/tickets/NEVER-CACHED/triage")
        assert r.status_code == 404
        assert "not cached" in r.json()["detail"]
        assert "sync first" in r.json()["detail"]

    def test_triage_route_returns_payload_when_cached(self, client):
        from server import _db
        self._seed_flaky_ticket(_db)
        r = client.get("/api/tickets/EX-1002/triage?instance_name=primary")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ticket"]["key"] == "EX-1002"
        assert body["owner"]["assignee"] == "alice@example.com"
        assert "Foo.py" in str(body["files_referenced"])

    def test_extract_file_paths_caps_at_limit(self, patched_server):
        # Stack trace mentioning the same file 100 times -> dedup'd to 1.
        text = "\n".join(["at foo/bar/Baz.py:42"] * 100)
        out = core_tickets._extract_file_paths(text)
        assert out == ["foo/bar/Baz.py"]

    def test_get_one_walks_instances_when_no_instance_name(self, patched_server):
        """Triage / detail panels call `get_one(key)` without an
        instance_name. The DB stores rows keyed by (instance_name,
        key), so the lookup MUST walk every configured instance --
        otherwise tickets cached under non-default instances 404 even
        though they're in the DB. This was a bug in Phase-1."""
        # Seed a row under instance 'company' (NOT the default '').
        patched_server._db.upsert_ticket(
            instance_name="company", key="EX-9999",
            summary="test", status="Open",
        )
        _configure_jira(patched_server._db, name="company",
                        email="me@x")
        # No instance_name passed -> walks instances and finds it.
        out = core_tickets.get_one("EX-9999")
        assert out is not None
        assert out["key"] == "EX-9999"
        # Triage by extension (uses get_one) also returns a result.
        report = core_tickets.triage("EX-9999")
        assert report is not None
        assert report["ticket"]["key"] == "EX-9999"

    def test_blame_empty_when_no_local_repos_configured(self, patched_server):
        """Default OSS install has no local-repo-paths -- blame is
        an empty list (not None) so the frontend can render
        "configure local repo paths" without a separate sentinel."""
        self._seed_flaky_ticket(patched_server._db)
        out = core_tickets.triage("EX-1002", instance_name="primary")
        assert out["blame"] == []

    def test_blame_resolves_recent_author_via_git_log(self, patched_server,
                                                      tmp_path):
        """When a file referenced in the description exists inside a
        configured local-repo path, run `git log -1` against it and
        report the author. We use a tmp git repo + an injected
        runner so the test never shells out to the user's real git."""
        # Seed a ticket whose description references a real file in
        # our tmp repo.
        repo = tmp_path / "fake-repo"
        repo.mkdir()
        (repo / "src").mkdir()
        target = repo / "src" / "Foo.py"
        target.write_text("// content")
        patched_server._db.upsert_ticket(
            instance_name="primary", key="FOO-1",
            summary="flaky", description="at src/Foo.py:42",
            status="Open",
        )
        _configure_jira(patched_server._db, name="primary",
                        email="me@example.com")
        # Configure the local-repo path so the helper finds Foo.py.
        patched_server._db.set_setting(
            core_settings.KEY_GIT_LOCAL_REPO_PATHS,
            {"acme/widget": str(repo)},
        )

        # Inject a fake `git log` output so we don't need a real git
        # repo + commits.
        sep = "\x1f"
        fake_stdout = sep.join([
            "deadbeefcafe1234567890",
            "Alice Doe",
            "alice@example.com",
            "2026-04-25T08:00:00Z",
            "fix: stub",
        ])

        class _Proc:
            def __init__(self, stdout: str):
                self.stdout = stdout
                self.returncode = 0

        calls: list[tuple] = []

        def fake_runner(args, cwd):
            calls.append((args, cwd))
            return _Proc(fake_stdout)

        out = core_tickets._git_blame_for_files(
            ["src/Foo.py"], runner=fake_runner)
        assert len(out) == 1
        row = out[0]
        assert row["file"] == "src/Foo.py"
        assert row["repo"] == "acme/widget"
        assert row["local_path"] == str(repo)
        assert row["author_name"] == "Alice Doe"
        assert row["author_email"] == "alice@example.com"
        assert row["commit"] == "deadbee"
        assert row["committed_at"] == "2026-04-25T08:00:00Z"
        assert row["subject"] == "fix: stub"
        # Verify we actually called git log with the right args + cwd.
        assert calls[0][1] == str(repo)
        assert "git" in calls[0][0]
        assert "log" in calls[0][0]
        assert "src/Foo.py" in calls[0][0]

    def test_blame_skips_files_not_in_any_configured_repo(self, patched_server,
                                                          tmp_path):
        """Files mentioned in the description but not present in any
        configured local repo are silently skipped -- the UI still
        shows the file path under `files_referenced`."""
        repo = tmp_path / "empty-repo"
        repo.mkdir()
        patched_server._db.set_setting(
            core_settings.KEY_GIT_LOCAL_REPO_PATHS,
            {"acme/widget": str(repo)},
        )
        ran = []

        def fake_runner(args, cwd):
            ran.append((args, cwd))
            class _P: stdout = ""; returncode = 0
            return _P()

        out = core_tickets._git_blame_for_files(
            ["nonexistent/path.py"], runner=fake_runner)
        assert out == []
        # We must NOT have shelled out -- the file existence check
        # short-circuits before the subprocess call.
        assert ran == []

    def test_blame_swallows_git_log_failure(self, patched_server, tmp_path):
        """A non-zero git log exit (corrupt repo, deleted file, etc.)
        is silently dropped instead of crashing the whole report."""
        repo = tmp_path / "broken-repo"
        repo.mkdir()
        target = repo / "x.py"
        target.write_text("")
        patched_server._db.set_setting(
            core_settings.KEY_GIT_LOCAL_REPO_PATHS,
            {"acme/widget": str(repo)},
        )

        def fake_runner(args, cwd):
            class _P: stdout = ""; returncode = 128
            return _P()

        out = core_tickets._git_blame_for_files(
            ["x.py"], runner=fake_runner)
        assert out == []

    def test_extract_file_paths_picks_up_bazel_targets(self, patched_server):
        """Some EX- 'Failing target detected' tickets carry
        Bazel-style targets like
        `//repo/images/extensions/widget:18.0.x-suite` instead of
        traditional file paths. The extractor surfaces the directory
        (without the `:label` suffix) so the git-blame helper can
        match it against a local repo's filesystem layout."""
        text = (
            "Failing target: //repo/images/extensions/widget:"
            "18.0.x-snapshot-widget-suite_independent_executor_"
            "cr_mode_suite as of 2026-04-29"
        )
        out = core_tickets._extract_file_paths(text)
        assert "repo/images/extensions/widget" in out

    def test_extract_file_paths_skips_single_segment_bazel(self, patched_server):
        """`//foo:bar` (single-segment) is too generic to be useful for
        owner lookup; we drop it to keep the signal:noise ratio high."""
        out = core_tickets._extract_file_paths("//foo:bar")
        assert "foo" not in out

    def test_mode_returns_most_common_non_empty(self, patched_server):
        assert core_tickets._mode([]) == ""
        assert core_tickets._mode(["", "", ""]) == ""
        assert core_tickets._mode(["a", "b", "a", ""]) == "a"
        # Tie-broken by first-occurrence so the result is deterministic.
        assert core_tickets._mode(["a", "b", "b", "a"]) == "a"

    def test_derive_owner_team_prefers_current_components(self, patched_server):
        out = core_tickets._derive_owner_team(
            current_components=["Widget Images"],
            similar_tickets=[
                {"components": ["Other Team"], "assignee_email": "x"},
            ],
            blame=[],
            bazel_targets=["repo/foo"],
        )
        assert out == "Widget Images"

    def test_derive_owner_team_falls_back_to_similar_ticket_components(
        self, patched_server,
    ):
        # Current ticket has no components; similar tickets DO -> use
        # the most-common one.
        out = core_tickets._derive_owner_team(
            current_components=[],
            similar_tickets=[
                {"components": ["Widget Backend"], "assignee_email": ""},
                {"components": ["Widget Backend"], "assignee_email": ""},
                {"components": ["UI"], "assignee_email": ""},
            ],
            blame=[],
            bazel_targets=["widget/foo"],
        )
        assert out == "Widget Backend"

    def test_derive_owner_team_falls_back_to_bazel_path(self, patched_server):
        out = core_tickets._derive_owner_team(
            current_components=[],
            similar_tickets=[],
            blame=[],
            bazel_targets=["widget/extensions/mesh"],
        )
        assert "widget" in out
        assert "Bazel" in out  # makes the inference source visible

    def test_derive_owner_team_returns_empty_when_no_signals(self, patched_server):
        out = core_tickets._derive_owner_team(
            current_components=[],
            similar_tickets=[],
            blame=[],
            bazel_targets=[],
        )
        assert out == ""

    def test_derive_test_owner_prefers_current_assignee(self, patched_server):
        out = core_tickets._derive_test_owner(
            current_assignee="alice@x",
            similar_tickets=[{"assignee_email": "bob@x"}],
            blame=[{"author_name": "Carol", "author_email": "carol@x"}],
        )
        assert out == "alice@x"

    def test_derive_test_owner_falls_back_to_mode_of_similar(self, patched_server):
        out = core_tickets._derive_test_owner(
            current_assignee="",
            similar_tickets=[
                {"assignee_email": "bob@x"},
                {"assignee_email": "bob@x"},
                {"assignee_email": "carol@x"},
            ],
            blame=[],
        )
        assert out == "bob@x"

    def test_derive_test_owner_skips_self_echo(self, patched_server):
        """When the JIRA assignee IS the requesting user (i.e., the
        person triaging is already on the ticket), echoing them back
        as 'most likely owner' is a useless tautology. Skip and look
        for the next-best signal (similar tickets / blame)."""
        # Self-echo case -> falls through to similar-ticket mode.
        out = core_tickets._derive_test_owner(
            current_assignee="me@x",
            similar_tickets=[
                {"assignee_email": "me@x"},   # also self -- ignored
                {"assignee_email": "alice@x"},
                {"assignee_email": "alice@x"},
            ],
            blame=[],
            self_email="me@x",
        )
        assert out == "alice@x"

    def test_derive_test_owner_skips_self_echo_falls_back_to_blame(
        self, patched_server,
    ):
        """No useful signal anywhere -> fall through all the way to
        blame author. Critically: self-only context does NOT echo
        back the requesting user."""
        out = core_tickets._derive_test_owner(
            current_assignee="me@x",
            similar_tickets=[{"assignee_email": "me@x"}],  # self only
            blame=[{"author_name": "Alice", "author_email": "alice@x"}],
            self_email="me@x",
        )
        assert "Alice" in out
        assert "alice@x" in out

    def test_derive_test_owner_last_resort_returns_self_when_no_alternatives(
        self, patched_server,
    ):
        """If every signal is just the requesting user AND blame is
        empty, surface the assignee anyway -- a tautology beats an
        empty cell."""
        out = core_tickets._derive_test_owner(
            current_assignee="me@x",
            similar_tickets=[],
            blame=[],
            self_email="me@x",
        )
        assert out == "me@x"

    def test_summary_keyword_extracts_meaningful_phrase(self, patched_server):
        """`_summary_keyword` strips bracket tags and stopwords so the
        JQL fallback search uses something specific enough."""
        kw = core_tickets._summary_keyword(
            "[ALT-1234] Fix OpenAI Config Parity Request"
        )
        # Should NOT include the bracket tag or "Fix" (too generic).
        assert "OpenAI" in kw
        assert "Parity" in kw
        assert "[SC-" not in kw
        # Returns empty string when the summary is too short / all
        # stopwords.
        assert core_tickets._summary_keyword("Fix bug") == ""

    def test_derive_test_owner_falls_back_to_blame_author(self, patched_server):
        out = core_tickets._derive_test_owner(
            current_assignee="",
            similar_tickets=[{"assignee_email": ""}],
            blame=[{"author_name": "Alex", "author_email": "bob@x"}],
        )
        assert "Alex" in out
        assert "bob@x" in out

    def test_extract_file_paths_does_not_match_mid_url(self, patched_server):
        """Wiki-style links like `[label|https://example.com/health///repo/foo:label]`
        must NOT be parsed as a Bazel target -- the leading `://` and
        the `///` between go-link and target both mean the captured
        path would carry URL fragments. The lookbehind + 'no consecutive
        slashes' rule excludes both."""
        text = "See [go/x|https://go/x///repo/foo/bar:label] for ..."
        out = core_tickets._extract_file_paths(text)
        # MUST NOT match the URL-embedded target.
        assert "x///repo/foo/bar" not in out
        for path in out:
            assert "://" not in path
            assert "//" not in path

    def test_extract_file_paths_skips_bare_classnames(self, patched_server):
        # `MyClass.py` (no `/`) is usually a class name from a
        # package-qualified path already captured elsewhere -- we
        # don't want it as a duplicate "file referenced" entry.
        text = "Failed: MyClass.py threw\n  at foo/bar/My.py:1"
        out = core_tickets._extract_file_paths(text)
        assert "foo/bar/My.py" in out
        assert "MyClass.py" not in out


class TestPhase2WriteActions:
    """Phase-2 write-side: comment / list_transitions / transition.
    Exercised at the core layer with a fake `http_post`/`http_get`
    so the tests never reach the network."""

    def _setup_instance(self, db):
        _configure_jira(db, email="me@example.com", name="primary")
        db.upsert_ticket(
            instance_name="primary", key="WRT-1",
            assignee_email="me@example.com",
        )

    def test_add_comment_posts_to_correct_url_with_auth(
        self, patched_server,
    ):
        self._setup_instance(patched_server._db)
        captured = {}

        def fake_post(url, headers, payload=None, timeout=15):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            return {"id": "10001", "body": payload["body"]}

        out = core_tickets.add_comment(
            "WRT-1", "ship it", http_post=fake_post,
        )
        assert out["id"] == "10001"
        assert "/rest/api/2/issue/WRT-1/comment" in captured["url"]
        assert captured["payload"] == {"body": "ship it"}
        # Auth header carries through from the configured instance.
        assert "Authorization" in captured["headers"]

    def test_add_comment_rejects_blank_body(self, patched_server):
        self._setup_instance(patched_server._db)
        import pytest
        with pytest.raises(ValueError, match="comment body"):
            core_tickets.add_comment("WRT-1", "   ")

    def test_add_comment_unknown_instance_404(self, patched_server):
        # Configured instance is "primary"; ask for "other" -> error.
        self._setup_instance(patched_server._db)
        import pytest
        with pytest.raises(ValueError, match="not configured"):
            core_tickets.add_comment(
                "WRT-1", "hi", instance_name="other",
            )

    def test_resolve_instance_uses_cached_row_when_no_override(
        self, patched_server,
    ):
        # Two configured instances; the ticket is cached under the
        # second one. resolve must pick the second.
        _configure_jira(patched_server._db, email="me@example.com",
                        name="primary")
        _configure_jira(patched_server._db, email="me@example",
                        name="example", base_url="https://issues.example.org/jira",
                        replace=False)
        patched_server._db.upsert_ticket(
            instance_name="example", key="EX-1",
        )
        captured = {}
        def fake_post(url, headers, payload=None, timeout=15):
            captured["url"] = url
            return {}
        core_tickets.add_comment("EX-1", "x", http_post=fake_post)
        # The second instance's base URL was used, not primary's.
        assert "issues.example.org" in captured["url"]

    def test_list_transitions_passes_through(self, patched_server):
        self._setup_instance(patched_server._db)
        def fake_get(url, headers, timeout=15):
            assert "/rest/api/2/issue/WRT-1/transitions" in url
            return {"transitions": [
                {"id": "21", "name": "Resolved"},
                {"id": "31", "name": "Closed"},
            ]}
        out = core_tickets.list_transitions("WRT-1", http_get=fake_get)
        assert [t["name"] for t in out] == ["Resolved", "Closed"]

    def test_transition_issue_posts_payload_with_resolution_and_comment(
        self, patched_server,
    ):
        self._setup_instance(patched_server._db)
        captured = {}

        def fake_post(url, headers, payload=None, timeout=15):
            captured["url"] = url
            captured["payload"] = payload
            return {}

        core_tickets.transition_issue(
            "WRT-1", "21",
            resolution="Fixed", comment="all done",
            http_post=fake_post,
        )
        assert "/transitions" in captured["url"]
        assert captured["payload"]["transition"] == {"id": "21"}
        assert captured["payload"]["fields"]["resolution"] == {"name": "Fixed"}
        assert captured["payload"]["update"]["comment"][0]["add"]["body"] == "all done"

    def test_transition_issue_requires_id(self, patched_server):
        self._setup_instance(patched_server._db)
        import pytest
        with pytest.raises(ValueError, match="transition_id"):
            core_tickets.transition_issue("WRT-1", "")


class TestEnrichForViewFallbacks:
    """Defensive arms in `enrich_for_view`: a DB error during the
    reverse-task lookup must not break the whole detail panel."""

    def test_db_exception_returns_empty_linked_tasks(
        self, patched_server, monkeypatch,
    ):
        # Simulate a DB error during find_tasks_by_ticket. The enricher
        # swallows it and returns an empty linked_tasks list, so the
        # ticket detail still renders.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ENR-X",
        )
        row = patched_server._db.get_ticket(
            "ENR-X", instance_name="primary",
        )

        def boom(*a, **kw):
            raise RuntimeError("db kaboom")
        monkeypatch.setattr(patched_server._db,
                            "find_tasks_by_ticket", boom)
        out = core_tickets.enrich_for_view(row)
        # Field is present but empty so the UI can iterate without
        # null-checks.
        assert out["linked_tasks"] == []


class TestSyncLegacyWrapper:
    """The legacy `sync()` wrapper around `sync_all` is what older
    routes / CLI callers use. The empty-instances branch in
    particular is easy to miss because new callers always go through
    `sync_all` directly."""

    def test_sync_empty_when_no_instances_configured(self, patched_server):
        # Returns the legacy {count, pruned, jql} shape with errors
        # captured -- no exception even when there's nothing to sync.
        import pytest
        # `sync_all` raises when no instances; legacy wrapper bubbles.
        with pytest.raises(ValueError, match="No JIRA"):
            core_tickets.sync()


class TestTrackEdgeCases:
    def test_track_requires_key(self, patched_server):
        import pytest
        with pytest.raises(ValueError, match="ticket key"):
            core_tickets.track("")


class TestSessionPromptIncludesInstance:
    """Ticket session system prompt mentions the instance when set
    so agent knows which JIRA the ticket lives in -- a EX-123 on a
    self-hosted Server instance vs a EX-123 on Atlassian Cloud have
    different auth contexts."""

    def test_prompt_mentions_instance_when_set(self):
        prompt = core_tickets._build_ticket_system_prompt({
            "key": "ABC-1", "summary": "fix it",
            "status": "Open", "issue_type": "Bug",
            "priority": "High", "url": "https://j.example/browse/ABC-1",
            "instance_name": "example",
            "description": "details",
        })
        assert "Instance:example" in prompt

    def test_prompt_omits_instance_when_empty(self):
        prompt = core_tickets._build_ticket_system_prompt({
            "key": "ABC-1", "summary": "fix it",
            "status": "Open", "issue_type": "Bug",
            "priority": "High", "url": "https://j.example/browse/ABC-1",
            "instance_name": "",
            "description": "",
        })
        assert "Instance:" not in prompt


class TestNormaliseInstance:
    """Coverage for the defensive parser that turns raw settings
    entries into instance dicts. The branches matter because a
    half-typed instance shouldn't blow up the whole tickets page;
    `_normalise_instance` returns None and the caller (`list_instances`)
    silently skips."""

    def test_non_dict_returns_none(self):
        assert core_tickets._normalise_instance("not-a-dict") is None
        assert core_tickets._normalise_instance(None) is None
        assert core_tickets._normalise_instance(["list"]) is None

    def test_missing_base_url_returns_none(self):
        # User saved a half-form: name + token but no base_url ->
        # skip silently.
        assert core_tickets._normalise_instance({
            "name": "x", "base_url": "", "api_token": "T",
        }) is None

    def test_missing_token_returns_none(self):
        assert core_tickets._normalise_instance({
            "name": "x", "base_url": "https://x.example", "api_token": "",
        }) is None

    def test_unknown_auth_type_falls_back_to_basic(self):
        # An open-source user might paste a bogus auth_type from an
        # outdated tutorial. Fall back to basic -- safer than letting
        # the call fail with `unknown auth_type`.
        out = core_tickets._normalise_instance({
            "name": "x", "base_url": "https://x.example",
            "api_token": "T", "auth_type": "kerberos",
        })
        assert out["auth_type"] == "basic"

    def test_blank_name_falls_back_to_base_url(self):
        # The instance dict is keyed on `name`. If the user left it
        # blank, base_url is the fallback so the primary-key stays
        # unique without dropping the whole row.
        out = core_tickets._normalise_instance({
            "name": "", "base_url": "https://x.example", "api_token": "T",
        })
        assert out["name"] == "https://x.example"

    def test_blank_jql_falls_back_to_default(self):
        out = core_tickets._normalise_instance({
            "name": "x", "base_url": "https://x.example",
            "api_token": "T", "jql": "",
        })
        from common import settings as _s
        assert out["jql"] == _s.DEFAULT_JIRA_JQL


class TestListInstancesDedup:
    """Defensive de-dup: if two settings entries share the same
    normalised name, only the first wins. Without this, the same
    name could get two cached-ticket buckets."""

    def test_dupe_names_collapse(self, patched_server):
        from common import settings as _s
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "p", "base_url": "https://x.example",
             "auth_type": "basic", "email": "a@x", "api_token": "T",
             "jql": "first"},
            {"name": "p", "base_url": "https://y.example",
             "auth_type": "basic", "email": "a@y", "api_token": "T",
             "jql": "second"},
        ])
        instances = core_tickets.list_instances()
        names = [i["name"] for i in instances]
        assert names == ["p"]
        assert instances[0]["jql"] == "first"


class TestResolveInstanceForTicket:
    """`_resolve_instance_for_ticket` is the multi-JIRA dispatcher
    that picks which instance to talk to for write actions. The
    lookup priority matters: explicit instance_name wins -> cached
    row's instance_name -> fall-back scan -> raise."""

    def test_explicit_name_404s_when_not_configured(self, patched_server):
        _configure_jira(patched_server._db, name="primary")
        import pytest
        with pytest.raises(ValueError, match="not configured"):
            core_tickets._resolve_instance_for_ticket(
                "ANY-1", instance_name="ghost",
            )

    def test_falls_back_to_cached_rows_instance_name(self, patched_server):
        # No `instance_name` arg, but the ticket is cached under
        # `secondary` -- resolver finds it via the cached row.
        _configure_jira(patched_server._db, name="primary")
        _configure_jira(patched_server._db, name="secondary",
                        base_url="https://other.atlassian",
                        replace=False)
        patched_server._db.upsert_ticket(
            instance_name="secondary", key="LOOKUP-1",
        )
        out = core_tickets._resolve_instance_for_ticket(
            "LOOKUP-1", instance_name="",
        )
        assert out["name"] == "secondary"

    def test_scan_fallback_when_cache_has_instance_unknown_to_settings(
        self, patched_server,
    ):
        # Edge case: cache has a row tagged with an old instance name
        # that has since been deleted from settings. Resolver walks
        # remaining instances and falls back to the for-loop scan.
        _configure_jira(patched_server._db, name="primary")
        # Cached row tagged with an instance NOT in current config.
        patched_server._db.upsert_ticket(
            instance_name="orphaned", key="ORPH-1",
        )
        # And a row for `primary` so the scan finds it eventually.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="ORPH-1",
        )
        out = core_tickets._resolve_instance_for_ticket(
            "ORPH-1", instance_name="",
        )
        assert out["name"] == "primary"

    def test_404s_when_no_instance_recognises_key(self, patched_server):
        _configure_jira(patched_server._db, name="primary")
        import pytest
        with pytest.raises(ValueError, match="not found in any"):
            core_tickets._resolve_instance_for_ticket(
                "TOTALLY-UNKNOWN", instance_name="",
            )


class TestTrackProbeFallback:
    """Phase-4 `track` probes each configured JIRA instance until
    one returns the issue. Already covered for the happy path; this
    test exercises the multi-instance fallback when the first
    instance doesn't recognise the key."""

    def test_track_falls_through_after_first_runtime_error(self, patched_server):
        # Two instances configured. The first raises (auth issue);
        # the second returns the issue. track must not stop on the
        # first failure -- one broken JIRA shouldn't block another.
        _configure_jira(patched_server._db, name="broken",
                        base_url="https://broken.example")
        _configure_jira(patched_server._db, name="working",
                        base_url="https://working.example",
                        replace=False)
        calls = []

        def fake_get(url, headers, timeout=15):
            calls.append(url)
            if "broken.example" in url:
                raise RuntimeError("JIRA returned 401: unauthorized")
            return {
                "key": "PROBE-1",
                "fields": {"summary": "found it",
                           "status": {"name": "Open"}},
            }
        out = core_tickets.track("PROBE-1", http_get=fake_get)
        assert out is not None
        assert out["summary"] == "found it"
        # Probed both instances.
        assert any("broken.example" in c for c in calls)
        assert any("working.example" in c for c in calls)


class TestInstanceCRUDRoute:
    """`PUT /api/tickets/instances/{name}` and the matching DELETE were
    completely uncovered. These two routes are the only programmatic
    way the Settings UI configures JIRA, so they have to be solid --
    a 422 path that silently saves bad config, or a 404 that doesn't
    drop the cache, would leave the user with a broken Tickets page
    they can't recover from without raw DB editing."""

    def test_upsert_creates_new_instance(self, client):
        from common import settings as _s
        # Pre-condition: no instances configured.
        assert (_s.get_value(_s.KEY_JIRA_INSTANCES) or []) == []
        r = client.put(
            "/api/tickets/instances/primary",
            json={
                "name": "primary",
                "base_url": "https://example.atlassian.net",
                "auth_type": "basic",
                "email": "alice@example.com",
                "api_token": "FAKE",
                "jql": "assignee = currentUser()",
            },
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True, "name": "primary"}
        instances = _s.get_value(_s.KEY_JIRA_INSTANCES) or []
        assert len(instances) == 1
        assert instances[0]["name"] == "primary"
        assert instances[0]["api_token"] == "FAKE"

    def test_upsert_replaces_same_name_in_place(self, client):
        from common import settings as _s
        # Seed with one instance, then upsert the same name.
        _s.set_value(_s.KEY_JIRA_INSTANCES, [{
            "name": "primary",
            "base_url": "https://old.example",
            "auth_type": "basic",
            "email": "old@example.com",
            "api_token": "OLD",
            "jql": "old",
        }])
        r = client.put(
            "/api/tickets/instances/primary",
            json={
                "name": "primary",
                "base_url": "https://new.example",
                "auth_type": "bearer",
                "api_token": "NEW",
            },
        )
        assert r.status_code == 200
        instances = _s.get_value(_s.KEY_JIRA_INSTANCES) or []
        assert len(instances) == 1
        assert instances[0]["base_url"] == "https://new.example"
        assert instances[0]["api_token"] == "NEW"
        assert instances[0]["auth_type"] == "bearer"

    def test_upsert_uses_url_name_when_body_disagrees(self, client):
        # Path arg `name` should win over `body.name` -- frontend can
        # leave either blank, so the URL is authoritative.
        r = client.put(
            "/api/tickets/instances/url_wins",
            json={
                "name": "body_loses",
                "base_url": "https://x.example",
                "auth_type": "basic",
                "email": "a@x.example",
                "api_token": "T",
            },
        )
        assert r.status_code == 200
        assert r.json()["name"] == "url_wins"
        from common import settings as _s
        assert _s.get_value(_s.KEY_JIRA_INSTANCES)[0]["name"] == "url_wins"

    def test_upsert_jql_defaults_when_blank(self, client):
        from common import settings as _s
        r = client.put(
            "/api/tickets/instances/needs_default",
            json={
                "name": "needs_default",
                "base_url": "https://x.example",
                "auth_type": "basic",
                "email": "a@x.example",
                "api_token": "T",
                # jql intentionally omitted
            },
        )
        assert r.status_code == 200
        instance = _s.get_value(_s.KEY_JIRA_INSTANCES)[0]
        assert instance["jql"] == _s.DEFAULT_JIRA_JQL

    def test_upsert_422_on_bad_auth_type(self, client):
        r = client.put(
            "/api/tickets/instances/x",
            json={
                "name": "x", "base_url": "https://x.example",
                "auth_type": "kerberos",  # not basic/bearer
                "api_token": "T",
            },
        )
        assert r.status_code == 422
        assert "auth_type" in r.json()["detail"]

    def test_upsert_422_on_missing_token(self, client):
        r = client.put(
            "/api/tickets/instances/x",
            json={
                "name": "x", "base_url": "https://x.example",
                "auth_type": "basic", "api_token": "",
            },
        )
        assert r.status_code == 422
        assert "api_token" in r.json()["detail"]

    def test_upsert_422_on_missing_base_url(self, client):
        r = client.put(
            "/api/tickets/instances/x",
            json={
                "name": "x", "base_url": "",
                "auth_type": "basic", "api_token": "T",
            },
        )
        assert r.status_code == 422
        assert "base_url" in r.json()["detail"]

    def test_upsert_422_on_blank_name(self, client):
        # Empty path arg AND empty body.name -> can't derive a name.
        r = client.put(
            "/api/tickets/instances/  ",
            json={
                "name": "  ", "base_url": "https://x.example",
                "auth_type": "basic", "api_token": "T",
            },
        )
        assert r.status_code == 422
        assert "name" in r.json()["detail"]

    def test_delete_removes_instance_and_drops_cached_tickets(self, client):
        from common import settings as _s
        from server import _db
        # Seed two instances + cached tickets for both.
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "alpha", "base_url": "https://a.example",
             "auth_type": "basic", "email": "x@a", "api_token": "T",
             "jql": "x"},
            {"name": "beta",  "base_url": "https://b.example",
             "auth_type": "basic", "email": "x@b", "api_token": "T",
             "jql": "x"},
        ])
        _db.upsert_ticket(instance_name="alpha", key="A-1", summary="alpha")
        _db.upsert_ticket(instance_name="beta",  key="B-1", summary="beta")

        r = client.delete("/api/tickets/instances/alpha")
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        names = [i["name"] for i in (_s.get_value(_s.KEY_JIRA_INSTANCES) or [])]
        assert names == ["beta"]
        # The deleted instance's cached tickets are pruned; beta's stay.
        assert _db.get_ticket("A-1", instance_name="alpha") is None
        assert _db.get_ticket("B-1", instance_name="beta") is not None

    def test_delete_404_for_unknown_instance(self, client):
        r = client.delete("/api/tickets/instances/never_configured")
        assert r.status_code == 404


class TestRouteErrorMappings:
    """Cover the error-branch arms of the Phase 2-5 routes that the
    happy-path tests don't reach. Each is a 1-line ValueError /
    RuntimeError -> HTTPException mapping; if the mapping silently
    drops, the frontend gets opaque 500s instead of actionable
    422/502s."""

    def test_sync_one_instance_404_when_not_configured(self, client):
        r = client.post("/api/tickets/sync/never_configured")
        assert r.status_code == 404
        assert "not configured" in r.json()["detail"]

    def test_sync_one_instance_502_when_jira_errors(self, client, monkeypatch):
        from common import settings as _s
        from adapters import jira as _jira_adapter
        _s.set_value(_s.KEY_JIRA_INSTANCES, [{
            "name": "broken", "base_url": "https://x.example",
            "auth_type": "basic", "email": "a@x", "api_token": "T",
            "jql": "x",
        }])

        def boom(*a, **kw):
            raise RuntimeError("JIRA returned 503: down")
        monkeypatch.setattr(_jira_adapter, "_http_get", boom)
        r = client.post("/api/tickets/sync/broken")
        assert r.status_code == 502
        assert "503" in r.json()["detail"]

    def test_get_transitions_404_when_unknown_instance(self, client):
        r = client.get(
            "/api/tickets/X-1/transitions?instance_name=missing",
        )
        assert r.status_code == 404

    def test_post_transition_422_when_id_blank(self, client):
        from common import settings as _s
        from server import _db
        _s.set_value(_s.KEY_JIRA_INSTANCES, [{
            "name": "p", "base_url": "https://x.example",
            "auth_type": "basic", "email": "a@x", "api_token": "T",
            "jql": "x",
        }])
        _db.upsert_ticket(instance_name="p", key="ERR-1")
        r = client.post(
            "/api/tickets/ERR-1/transition",
            json={"transition_id": ""},
        )
        assert r.status_code == 422
        assert "transition_id" in r.json()["detail"]

    def test_post_comment_502_on_jira_error(self, client, monkeypatch):
        from common import settings as _s
        from server import _db
        from adapters import jira as _jira_adapter
        _s.set_value(_s.KEY_JIRA_INSTANCES, [{
            "name": "p", "base_url": "https://x.example",
            "auth_type": "basic", "email": "a@x", "api_token": "T",
            "jql": "x",
        }])
        _db.upsert_ticket(instance_name="p", key="ERR-2")

        def boom(*a, **kw):
            raise RuntimeError("JIRA returned 401: unauthorized")
        monkeypatch.setattr(_jira_adapter, "_http_post", boom)

        r = client.post(
            "/api/tickets/ERR-2/comment", json={"body": "hi"},
        )
        assert r.status_code == 502
        assert "401" in r.json()["detail"]

    def test_get_ticket_404_message_includes_instance_name(self, client):
        # When the route is called with ?instance_name=, the 404
        # detail must scope to that instance so the user knows
        # which JIRA was checked.
        r = client.get("/api/tickets/X-99?instance_name=primary")
        assert r.status_code == 404
        assert "primary" in r.json()["detail"]


class TestPhase4Track:
    """`common.tickets.track` and the `POST /api/tickets/{key}/track`
    route -- the auto-add-on-click path used by `<TicketLink>` so a
    ticket key referenced anywhere in the app lands in the cache the
    first time the user clicks it."""

    def _instance_basic(self, db, name="primary"):
        _configure_jira(db, email="me@example.com", name=name)

    def test_returns_cached_row_without_jira_round_trip(self, patched_server):
        self._instance_basic(patched_server._db)
        patched_server._db.upsert_ticket(
            instance_name="primary", key="CACHED-1",
            summary="already here",
        )

        def fail_get(*a, **kw):
            raise AssertionError("track should not call JIRA when cached")
        out = core_tickets.track("CACHED-1", http_get=fail_get)
        assert out is not None
        assert out["key"] == "CACHED-1"
        assert out["summary"] == "already here"

    def test_fetches_from_jira_when_uncached(self, patched_server):
        self._instance_basic(patched_server._db)

        def fake_get(url, headers, timeout=15):
            assert "/rest/api/2/issue/NEW-1" in url
            return {
                "key": "NEW-1",
                "fields": {
                    "summary": "fresh from JIRA",
                    "status": {"name": "Open"},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": "Medium"},
                },
            }
        out = core_tickets.track("NEW-1", http_get=fake_get)
        assert out is not None
        assert out["key"] == "NEW-1"
        assert out["summary"] == "fresh from JIRA"
        # Row is now persisted -- a second call hits the cache (no JIRA).
        def fail_get(*a, **kw):
            raise AssertionError("second call should hit cache")
        out2 = core_tickets.track("NEW-1", http_get=fail_get)
        assert out2["summary"] == "fresh from JIRA"

    def test_returns_none_when_no_instance_recognises_key(
        self, patched_server,
    ):
        self._instance_basic(patched_server._db)
        import urllib.error

        def fake_get(url, headers, timeout=15):
            raise urllib.error.HTTPError(
                url=url, code=404, msg="Not Found",
                hdrs=None, fp=None,  # type: ignore[arg-type]
            )
        out = core_tickets.track("GONE-99", http_get=fake_get)
        assert out is None

    def test_returns_none_when_no_instances_configured(
        self, patched_server,
    ):
        # No JIRA configured at all -> nothing to track against.
        assert core_tickets.track("ANY-1") is None

    def test_route_404s_when_track_returns_none(self, client, monkeypatch):
        self._instance_basic(__import__("app_state")._db)
        from adapters import jira as _jira_adapter
        import urllib.error

        def fake_get(*a, **kw):
            raise urllib.error.HTTPError(
                url="x", code=404, msg="Not Found", hdrs=None,
                fp=None,  # type: ignore[arg-type]
            )
        monkeypatch.setattr(_jira_adapter, "_http_get", fake_get)
        r = client.post("/api/tickets/MISSING/track", json={})
        assert r.status_code == 404

    def test_route_returns_enriched_payload_for_freshly_tracked(
        self, client, monkeypatch,
    ):
        self._instance_basic(__import__("app_state")._db)
        from adapters import jira as _jira_adapter

        def fake_get(url, headers, timeout=15):
            assert "/rest/api/2/issue/SHINY-1" in url
            return {
                "key": "SHINY-1",
                "fields": {
                    "summary": "new",
                    "status": {"name": "Open"},
                    "labels": ["urgent"],
                },
            }
        monkeypatch.setattr(_jira_adapter, "_http_get", fake_get)
        r = client.post("/api/tickets/SHINY-1/track", json={})
        assert r.status_code == 200
        body = r.json()
        assert body["key"] == "SHINY-1"
        # The payload is view-enriched.
        assert body["labels"] == ["urgent"]

    def test_track_route_422_on_value_error(self, client, monkeypatch):
        # `common.tickets.track` raises ValueError when called with a
        # blank key / unknown instance_name. The route must surface as
        # 422 (validation), not 500.
        self._instance_basic(__import__("app_state")._db)
        from common import tickets as core_tickets

        def fake_track(*a, **kw):
            raise ValueError("instance_name 'ghost' is not configured")
        monkeypatch.setattr(core_tickets, "track", fake_track)
        r = client.post(
            "/api/tickets/X-1/track", json={"instance_name": "ghost"},
        )
        assert r.status_code == 422
        assert "ghost" in r.json()["detail"]

    def test_track_route_502_on_jira_runtime_error(self, client, monkeypatch):
        # When JIRA itself is broken, track() bubbles a RuntimeError
        # from the adapter. Route must surface as 502 so the frontend
        # can show "JIRA unreachable" instead of a generic 500.
        self._instance_basic(__import__("app_state")._db)
        from common import tickets as core_tickets

        def fake_track(*a, **kw):
            raise RuntimeError("JIRA returned 503: backend unreachable")
        monkeypatch.setattr(core_tickets, "track", fake_track)
        r = client.post("/api/tickets/X-1/track", json={})
        assert r.status_code == 502
        assert "503" in r.json()["detail"]

    def test_get_transitions_502_on_jira_runtime_error(self, client, monkeypatch):
        # The transitions endpoint must map adapter RuntimeError to
        # 502 (network / upstream JIRA failure) so the Resolve dropdown
        # can show "JIRA error" instead of a noisy 500.
        from common import settings as _s
        from server import _db
        from common import tickets as core_tickets
        _s.set_value(_s.KEY_JIRA_INSTANCES, [{
            "name": "p", "base_url": "https://x.example",
            "auth_type": "basic", "email": "a@x", "api_token": "T",
            "jql": "x",
        }])
        _db.upsert_ticket(instance_name="p", key="TR-1")

        def fake_list(*a, **kw):
            raise RuntimeError("JIRA returned 500: server error")
        monkeypatch.setattr(core_tickets, "list_transitions", fake_list)

        r = client.get("/api/tickets/TR-1/transitions?instance_name=p")
        assert r.status_code == 502
        assert "500" in r.json()["detail"]


class TestPhase3SyncEvents:
    """`sync_one` emits `ticket.created` / `ticket.updated` so the
    frontend can repaint live as the JIRA poller runs. Persistence is
    off (these are SSE-only signals, not user-visible notifications)."""

    def _instance(self):
        return {
            "name": "primary",
            "base_url": "https://example.atlassian.net",
            "auth_type": "basic",
            "email": "me@example.com",
            "api_token": "FAKE",
            "jql": "assignee = currentUser()",
        }

    def _capture_events(self, monkeypatch):
        events = []
        import app_state
        original = app_state.emit_event

        def spy(event_type, data, persist=True):
            events.append({"type": event_type, "data": data,
                           "persist": persist})
            # Don't call through -- the real bus tries to write to a
            # SQLite events table that the patched_server fixture has
            # already torn down by the time some tests fire. Capturing
            # the call shape is enough.
        monkeypatch.setattr(app_state, "emit_event", spy)
        return events, original

    def test_sync_emits_created_for_new_ticket(self, patched_server,
                                                monkeypatch):
        inst = self._instance()
        events, _ = self._capture_events(monkeypatch)

        def fake_get(url, headers, timeout=15):
            return {"issues": [{
                "key": "EVT-1",
                "fields": {
                    "summary": "first", "status": {"name": "Open"},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": "Medium"},
                    "project": {"key": "EVT"},
                },
            }]}
        core_tickets.sync_one(inst, http_get=fake_get)
        kinds = [e["type"] for e in events]
        assert "ticket.created" in kinds
        # No ticket.updated for a brand-new key.
        assert "ticket.updated" not in kinds
        # Persist=False -- these are push-only, not feed entries.
        assert all(e["persist"] is False for e in events
                   if e["type"].startswith("ticket."))

    def test_sync_emits_updated_when_status_changes(self, patched_server,
                                                     monkeypatch):
        inst = self._instance()
        # Seed prior state.
        patched_server._db.upsert_ticket(
            instance_name="primary", key="EVT-2",
            summary="thing", status="Open", priority="Medium",
            issue_type="Bug",
        )
        events, _ = self._capture_events(monkeypatch)

        def fake_get(url, headers, timeout=15):
            return {"issues": [{
                "key": "EVT-2",
                "fields": {
                    "summary": "thing",
                    "status": {"name": "In Progress"},  # CHANGED
                    "priority": {"name": "Medium"},
                    "issuetype": {"name": "Bug"},
                    "project": {"key": "EVT"},
                },
            }]}
        core_tickets.sync_one(inst, http_get=fake_get)
        kinds = [e["type"] for e in events]
        assert "ticket.updated" in kinds
        upd = next(e for e in events if e["type"] == "ticket.updated")
        assert upd["data"]["ticket_key"] == "EVT-2"
        assert "status" in (upd["data"].get("changes") or [])

    def test_sync_no_event_when_nothing_tracked_changed(
        self, patched_server, monkeypatch,
    ):
        inst = self._instance()
        # Same status / priority / summary as the prior row -> no
        # event. Only synced_at differs, which is intentionally not
        # tracked (would emit on every tick).
        patched_server._db.upsert_ticket(
            instance_name="primary", key="EVT-3",
            summary="x", status="Open", priority="Medium",
            issue_type="Bug", updated_at="2026-04-26T10:00:00",
        )
        events, _ = self._capture_events(monkeypatch)

        def fake_get(url, headers, timeout=15):
            return {"issues": [{
                "key": "EVT-3",
                "fields": {
                    "summary": "x", "status": {"name": "Open"},
                    "priority": {"name": "Medium"},
                    "issuetype": {"name": "Bug"},
                    "project": {"key": "EVT"},
                    "updated": "2026-04-26T10:00:00",
                },
            }]}
        core_tickets.sync_one(inst, http_get=fake_get)
        ticket_events = [e for e in events
                         if e["type"].startswith("ticket.")]
        assert ticket_events == []

    def test_sync_one_returns_created_and_updated_counts(
        self, patched_server, monkeypatch,
    ):
        inst = self._instance()
        patched_server._db.upsert_ticket(
            instance_name="primary", key="OLD-1",
            summary="x", status="Open",
        )
        # Stub emit_event so the test focuses on the return shape.
        import app_state
        monkeypatch.setattr(app_state, "emit_event",
                            lambda *a, **kw: None)

        def fake_get(url, headers, timeout=15):
            return {"issues": [
                {"key": "OLD-1",
                 "fields": {"summary": "x",
                            "status": {"name": "Done"}}},  # changed
                {"key": "NEW-1",
                 "fields": {"summary": "fresh",
                            "status": {"name": "Open"}}},  # new
            ]}
        out = core_tickets.sync_one(inst, http_get=fake_get)
        assert out["count"] == 2
        assert out["created"] == 1
        assert out["updated"] == 1


class TestPhase2WriteRoutes:
    """End-to-end through the HTTP boundary so the route mappings
    (ValueError -> 422, RuntimeError -> 502) are covered."""

    def _setup(self, db):
        _configure_jira(db, email="me@example.com", name="primary")
        db.upsert_ticket(
            instance_name="primary", key="RT-1",
            assignee_email="me@example.com",
        )

    def test_post_comment_route(self, client, monkeypatch):
        self._setup(client.app.state if False else __import__(
            "app_state")._db)
        from adapters import jira as _jira_adapter

        def fake_post(url, headers, payload=None, timeout=15):
            return {"id": "11", "body": payload["body"]}
        monkeypatch.setattr(_jira_adapter, "_http_post", fake_post)

        r = client.post(
            "/api/tickets/RT-1/comment",
            json={"body": "looks good"},
        )
        assert r.status_code == 200
        assert r.json()["id"] == "11"

    def test_post_comment_route_blank_body_422(self, client):
        self._setup(__import__("app_state")._db)
        r = client.post(
            "/api/tickets/RT-1/comment", json={"body": ""},
        )
        assert r.status_code == 422

    def test_get_transitions_route(self, client, monkeypatch):
        self._setup(__import__("app_state")._db)
        from adapters import jira as _jira_adapter

        def fake_get(url, headers, timeout=15):
            return {"transitions": [{"id": "21", "name": "Resolved"}]}
        monkeypatch.setattr(_jira_adapter, "_http_get", fake_get)

        r = client.get("/api/tickets/RT-1/transitions")
        assert r.status_code == 200
        assert r.json()["transitions"][0]["name"] == "Resolved"

    def test_post_transition_route(self, client, monkeypatch):
        self._setup(__import__("app_state")._db)
        from adapters import jira as _jira_adapter
        captured = {}

        def fake_post(url, headers, payload=None, timeout=15):
            captured["payload"] = payload
            return {}
        monkeypatch.setattr(_jira_adapter, "_http_post", fake_post)

        r = client.post(
            "/api/tickets/RT-1/transition",
            json={"transition_id": "21", "resolution": "Fixed"},
        )
        assert r.status_code == 200
        assert captured["payload"]["transition"]["id"] == "21"
        assert captured["payload"]["fields"]["resolution"]["name"] == "Fixed"

    def test_post_transition_route_502_on_jira_error(
        self, client, monkeypatch,
    ):
        self._setup(__import__("app_state")._db)
        from adapters import jira as _jira_adapter

        def fake_post(*a, **kw):
            raise RuntimeError("JIRA returned 403: forbidden")
        monkeypatch.setattr(_jira_adapter, "_http_post", fake_post)

        r = client.post(
            "/api/tickets/RT-1/transition",
            json={"transition_id": "21"},
        )
        assert r.status_code == 502
        assert "403" in r.json()["detail"]


# ---- HTTP API ----

class TestTicketsApi:
    def test_list_endpoint_returns_configured_flag(self, client):
        resp = client.get("/api/tickets")
        assert resp.status_code == 200
        body = resp.json()
        assert "tickets" in body
        assert body["configured"] is False

    def test_list_endpoint_after_configure(self, client, patched_server):
        _configure_jira(patched_server._db)
        resp = client.get("/api/tickets")
        assert resp.json()["configured"] is True

    def test_sync_422_when_not_configured(self, client):
        assert client.post("/api/tickets/sync").status_code == 422

    def test_sync_collects_per_instance_errors(self, client, patched_server,
                                                 monkeypatch):
        # Multi-instance: one bad JIRA shouldn't fail the whole call.
        # We get a 200 response with the error captured in `errors`.
        _configure_jira(patched_server._db)

        def boom(*a, **kw):
            raise RuntimeError("simulated JIRA outage")
        monkeypatch.setattr("common.tickets._jira.search_issues", boom)
        resp = client.post("/api/tickets/sync")
        assert resp.status_code == 200
        body = resp.json()
        assert body["errors"]
        assert "outage" in body["errors"][0]["error"]


# ---- open_session_for_ticket ----

class TestOpenSessionForTicket:
    def test_session_name_format(self):
        assert core_tickets.session_name_for_ticket("ABC-1") == "ticket-ABC-1"

    def test_open_session_invokes_launcher_with_agent_argv(self, patched_server):
        # Seed a ticket so the cache lookup succeeds.
        patched_server._db.upsert_ticket(
            key="OPS-99", summary="Audit logging",
            description="add audit logs to admin endpoints",
            status="Open", url="https://j.example/browse/OPS-99",
        )
        captured = {}
        def fake_launch(name, working_dir, argv):
            captured["name"] = name
            captured["dir"] = working_dir
            captured["argv"] = argv
        out = core_tickets.open_session_for_ticket(
            "OPS-99", launcher=fake_launch,
        )
        # Multi-instance: dict carries instance_name (empty for the
        # legacy/un-instanced path) and prompt_sent (False -- no
        # custom_prompt was supplied).
        assert out == {"session": "ticket-OPS-99", "new": True,
                       "ticket_key": "OPS-99", "instance_name": "",
                       "prompt_sent": False}
        assert captured["name"] == "ticket-OPS-99"
        assert captured["argv"][0] in ("agent", "claude")  # active agent
        assert "-n" in captured["argv"]
        # The system prompt carries the ticket summary.
        sys_prompt_idx = captured["argv"].index("--append-system-prompt") + 1
        sys_prompt = captured["argv"][sys_prompt_idx]
        assert "OPS-99" in sys_prompt
        assert "Audit logging" in sys_prompt

    def test_open_session_404_for_missing_ticket(self, client):
        # No ticket in cache.
        resp = client.post("/api/tickets/NOPE-1/session")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_open_session_pastes_custom_prompt(self, patched_server):
        """Phase-5 action buttons supply a `custom_prompt` that the
        executor pastes into the session post-launch. Tests inject a
        fake `paste` so they don't shell out to tmux."""
        patched_server._db.upsert_ticket(
            key="ACT-1", summary="some flaky test",
        )
        pastes = []

        def fake_launch(*a, **kw): pass
        def fake_paste(name, text):
            pastes.append((name, text))

        out = core_tickets.open_session_for_ticket(
            "ACT-1",
            launcher=fake_launch,
            custom_prompt="/yh-fix-flaky-test ACT-1",
            paste=fake_paste,
        )
        assert out["prompt_sent"] is True
        assert pastes == [("ticket-ACT-1", "/yh-fix-flaky-test ACT-1")]

    def test_open_session_route_accepts_custom_prompt(
        self, client, monkeypatch,
    ):
        from server import _db
        _db.upsert_ticket(key="RT-ACT-1", summary="bug investigation")
        # Stub the tmux paste path so the route doesn't actually
        # touch the session.
        from adapters import tmux as _tmux
        captured = {}
        monkeypatch.setattr(_tmux, "session_exists", lambda n: False)
        monkeypatch.setattr(_tmux, "launch_session_argv",
                            lambda *a, **kw: None)
        monkeypatch.setattr(_tmux, "wait_until_ready",
                            lambda *a, **kw: True)

        def fake_paste(name, text):
            captured["paste"] = (name, text)
        monkeypatch.setattr(_tmux, "paste_text", fake_paste)

        resp = client.post(
            "/api/tickets/RT-ACT-1/session",
            json={"custom_prompt": "Investigate RT-ACT-1: ..."},
        )
        assert resp.status_code == 200
        assert resp.json()["prompt_sent"] is True
        assert captured["paste"][0] == "ticket-RT-ACT-1"
        assert "RT-ACT-1" in captured["paste"][1]

    def test_build_ticket_system_prompt_truncates_long_descriptions(self):
        ticket = {
            "key": "X-1", "summary": "s", "status": "Open",
            "issue_type": "Bug", "priority": "High", "url": "u",
            "description": "X" * 5000,
        }
        out = core_tickets._build_ticket_system_prompt(ticket)
        assert "[truncated]" in out
        # Trimmed body is ~1500 + header overhead, well under 5000.
        assert len(out) < 2200


class TestSyncOneEmitException:
    """`_emit_ticket_event` swallows event-bus errors so a broken
    listener can't tank the entire sync. Cover the except branch."""

    def test_emit_event_exception_does_not_propagate(self, patched_server,
                                                       monkeypatch, capsys):
        import app_state

        def boom(*a, **kw):
            raise RuntimeError("event bus down")
        monkeypatch.setattr(app_state, "emit_event", boom)
        # Should not raise -- the print fallback fires instead.
        core_tickets._emit_ticket_event(
            "ticket.created",
            {"key": "X-1", "summary": "s", "status": "Open",
             "instance_name": "primary"},
            changes=[],
        )
        captured = capsys.readouterr().out
        assert "emit ticket.created for X-1 failed" in captured


class TestNonStringDescription:
    """JIRA Cloud (v3) returns Atlassian Document Format (ADF) for
    `description` -- a dict, not a string. The mapper coerces it via
    str() so the cache column never blows up."""

    def test_dict_description_is_coerced_to_string(self):
        adf_doc = {"type": "doc", "version": 1, "content": []}
        instance = {
            "name": "cloud",
            "base_url": "https://x.example",
            "auth_type": "basic",
            "email": "a@x", "api_token": "T",
            "jql": "x",
        }
        out = core_tickets._normalise_issue(
            issue={
                "key": "ADF-1",
                "fields": {
                    "summary": "s", "status": {"name": "Open"},
                    "issuetype": {"name": "Bug"},
                    "priority": {"name": "Medium"},
                    "description": adf_doc,
                    "project": {"key": "ADF"},
                },
            },
            instance=instance,
        )
        # description must be a string in the cache row.
        assert isinstance(out["description"], str)
        assert out["description"]  # non-empty


class TestTrackCacheHits:
    """`track` short-circuits to the cache when a row already exists,
    skipping the JIRA round-trip. Two cache-hit branches: with an
    explicit instance, and the implicit walk over all instances."""

    def test_explicit_instance_cache_hit_skips_jira(self, patched_server):
        _configure_jira(patched_server._db, name="primary")
        patched_server._db.upsert_ticket(
            instance_name="primary", key="CACHE-1", summary="hit",
        )
        # If the JIRA round-trip fired we'd see the http_get raise.
        def boom(*a, **kw):
            raise AssertionError("track should not call JIRA on cache hit")
        out = core_tickets.track("CACHE-1", instance_name="primary",
                                 http_get=boom)
        assert out is not None
        assert out["key"] == "CACHE-1"
        assert out["summary"] == "hit"

    def test_implicit_walk_cache_hit_skips_jira(self, patched_server):
        _configure_jira(patched_server._db, name="primary")
        patched_server._db.upsert_ticket(
            instance_name="primary", key="CACHE-2", summary="walked",
        )
        def boom(*a, **kw):
            raise AssertionError("track should not call JIRA on cache hit")
        # No instance_name -> walks list_instances() until match.
        out = core_tickets.track("CACHE-2", http_get=boom)
        assert out["key"] == "CACHE-2"


class TestSessionNameForTicket:
    def test_includes_instance_name_when_present(self):
        # Two JIRAs with overlapping keys (e.g. PROJ-1 in both) get
        # distinct sessions by including the instance prefix.
        out = core_tickets.session_name_for_ticket(
            "PROJ-1", instance_name="cloud",
        )
        assert out == "ticket-cloud-PROJ-1"

    def test_omits_instance_name_when_blank(self):
        # Legacy single-JIRA path -- short session name for back-compat.
        assert core_tickets.session_name_for_ticket("PROJ-1") == "ticket-PROJ-1"


class TestNormaliseInstance:
    """`_normalise_instance` is the gatekeeper for `list_instances` --
    it filters out raw settings entries that wouldn't be usable. The
    error paths are what keep a half-edited config from blowing up
    every ticket-page render."""

    def test_returns_none_for_non_dict(self):
        assert core_tickets._normalise_instance("not a dict") is None
        assert core_tickets._normalise_instance(None) is None
        assert core_tickets._normalise_instance(["a", "list"]) is None

    def test_returns_none_when_base_url_missing(self):
        assert core_tickets._normalise_instance(
            {"name": "x", "api_token": "T"},
        ) is None

    def test_returns_none_when_api_token_missing(self):
        assert core_tickets._normalise_instance(
            {"name": "x", "base_url": "https://x.example"},
        ) is None

    def test_falls_back_to_basic_auth_for_unknown_auth_type(self):
        out = core_tickets._normalise_instance({
            "name": "x", "base_url": "https://x.example",
            "api_token": "T", "auth_type": "kerberos",
        })
        assert out is not None
        assert out["auth_type"] == "basic"

    def test_list_instances_skips_unnormalisable_entries(self, patched_server):
        # A half-typed entry in the settings list (missing api_token)
        # must NOT poison list_instances() -- it should be skipped so
        # the rest of the configured instances still load.
        from common import settings as _s
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "broken", "base_url": "https://b.example"},  # no token
            {"name": "good", "base_url": "https://g.example",
             "auth_type": "basic", "email": "x@x", "api_token": "T",
             "jql": "x"},
        ])
        out = core_tickets.list_instances()
        names = [i["name"] for i in out]
        assert "broken" not in names
        assert "good" in names


class TestTicketDiffEmptyPrev:
    """`_ticket_diff` short-circuits when there's no prior cached
    record -- a 'created' ticket has nothing to diff against, so the
    list of changed fields is conventionally empty."""

    def test_returns_empty_list_when_prev_is_none(self):
        out = core_tickets._ticket_diff(None, {"summary": "anything"})
        assert out == []


class TestSyncLegacyAllFail:
    """`sync()` is the back-compat wrapper around `sync_all`. When
    every configured instance fails, `sync_all` returns successfully
    with an empty `instances` list and the per-instance errors. The
    legacy wrapper must surface as `{count: 0, pruned: 0, ...}` rather
    than crash on `out['instances'][0]`."""

    def test_returns_legacy_shape_when_every_instance_fails(
        self, patched_server, monkeypatch,
    ):
        from common import settings as _s
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "broken", "base_url": "https://x.example",
             "auth_type": "basic", "email": "a@x", "api_token": "T",
             "jql": "x"},
        ])
        # Force every sync_one call to error out.
        def boom(*a, **kw):
            raise RuntimeError("JIRA down")
        monkeypatch.setattr(core_tickets, "sync_one", boom)
        out = core_tickets.sync()
        assert out["count"] == 0
        assert out["pruned"] == 0
        assert out["jql"] == ""
        assert out["errors"] and out["errors"][0]["name"] == "broken"


class TestTrackFiltersInstanceName:
    """When `track` is called with `instance_name=` and the named
    instance is in the list, JIRA must only be probed against THAT
    instance (not every configured one). Otherwise a typo'd
    instance_name would leak the ticket query to the wrong JIRA."""

    def test_track_filters_candidates_to_named_instance(self, patched_server):
        from common import settings as _s
        # Two instances; expect ONLY 'beta' to be probed.
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "alpha", "base_url": "https://a.example",
             "auth_type": "basic", "email": "x@x", "api_token": "T",
             "jql": "x"},
            {"name": "beta", "base_url": "https://b.example",
             "auth_type": "basic", "email": "x@x", "api_token": "T",
             "jql": "x"},
        ])
        seen_urls = []

        def fake_get(url, headers, timeout=15):
            seen_urls.append(url)
            return {"key": "BETA-1", "fields": {"summary": "from beta"}}

        out = core_tickets.track(
            "BETA-1", instance_name="beta", http_get=fake_get,
        )
        assert out is not None
        # Exactly one probe -- and it MUST be against beta's base_url.
        assert len(seen_urls) == 1
        assert "b.example" in seen_urls[0]
        assert "a.example" not in seen_urls[0]


class TestResolveInstanceForTicket:
    """`_resolve_instance_for_ticket` is the routing layer for write
    actions (comment / transition). Its branches: explicit instance,
    cached ticket's instance, walk-and-find, or raise."""

    def _setup_two_instances(self, patched_server):
        from common import settings as _s
        _s.set_value(_s.KEY_JIRA_INSTANCES, [
            {"name": "alpha", "base_url": "https://a.example",
             "auth_type": "basic", "email": "x@x", "api_token": "T",
             "jql": "x"},
            {"name": "beta", "base_url": "https://b.example",
             "auth_type": "basic", "email": "x@x", "api_token": "T",
             "jql": "x"},
        ])

    def test_explicit_instance_returns_matching_entry(self, patched_server):
        self._setup_two_instances(patched_server)
        out = core_tickets._resolve_instance_for_ticket(
            "X-1", instance_name="beta",
        )
        assert out["name"] == "beta"
        assert "b.example" in out["base_url"]

    def test_walks_instances_to_find_cached_ticket(self, patched_server):
        # No explicit instance_name -> resolver walks the configured
        # list and returns the instance whose cache has the ticket.
        self._setup_two_instances(patched_server)
        patched_server._db.upsert_ticket(
            instance_name="beta", key="ROUTED-1", summary="lives in beta",
        )
        out = core_tickets._resolve_instance_for_ticket(
            "ROUTED-1", instance_name="",
        )
        assert out["name"] == "beta"

    def test_raises_when_ticket_unknown_to_all_instances(self, patched_server):
        # Walk found nothing -> ValueError so the route layer 404s.
        self._setup_two_instances(patched_server)
        import pytest
        with pytest.raises(ValueError, match="not found in any"):
            core_tickets._resolve_instance_for_ticket(
                "GHOST-1", instance_name="",
            )


class TestPasteFallbacks:
    """`open_session_for_ticket` paste path: tmux readiness check ->
    paste, with an exception-swallowing wrapper so a slow tmux can't
    break the route."""

    def _setup_ticket(self, patched_server):
        patched_server._db.upsert_ticket(
            instance_name="", key="PST-1", summary="paste test",
        )

    def test_paste_runs_even_when_wait_until_ready_returns_false(
        self, patched_server, monkeypatch,
    ):
        # If wait_until_ready times out, the implementation pastes
        # anyway -- agent will accept the input once the prompt
        # eventually appears. Without this fallback the user's prompt
        # would be silently dropped on slow systems.
        from adapters import tmux as _tmux
        self._setup_ticket(patched_server)
        pastes = []
        monkeypatch.setattr(_tmux, "session_exists", lambda n: False)
        monkeypatch.setattr(_tmux, "launch_session_argv",
                            lambda *a, **kw: None)
        monkeypatch.setattr(_tmux, "wait_until_ready",
                            lambda *a, **kw: False)
        monkeypatch.setattr(_tmux, "paste_text",
                            lambda n, t: pastes.append((n, t)))
        out = core_tickets.open_session_for_ticket(
            "PST-1", custom_prompt="hi",
        )
        assert out["prompt_sent"] is True
        assert pastes == [("ticket-PST-1", "hi")]

    def test_paste_swallows_unexpected_exception(self, patched_server,
                                                  monkeypatch, capsys):
        # If the paste path raises (tmux gone, signal, whatever) the
        # session still came up -- we must NOT raise; just log and
        # let the caller see prompt_sent=True (the spawn succeeded).
        from adapters import tmux as _tmux
        self._setup_ticket(patched_server)
        monkeypatch.setattr(_tmux, "session_exists", lambda n: False)
        monkeypatch.setattr(_tmux, "launch_session_argv",
                            lambda *a, **kw: None)

        def boom(*a, **kw):
            raise RuntimeError("tmux is on fire")
        monkeypatch.setattr(_tmux, "wait_until_ready", boom)
        out = core_tickets.open_session_for_ticket(
            "PST-1", custom_prompt="hi",
        )
        assert out["session"] == "ticket-PST-1"
        captured = capsys.readouterr().out
        assert "paste prompt for PST-1 failed" in captured
