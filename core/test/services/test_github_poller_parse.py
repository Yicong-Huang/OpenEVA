"""Tests for _parse_notification_line from routes/events.py."""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# Import after path setup. The module starts a background thread on import,
# which is unavoidable but harmless in tests.
from services.github_poller import _parse_notification_line


class TestParseNotificationLine:
    """Tests for parsing tab-delimited GitHub notification lines."""

    def _make_line(self, nid="123", reason="comment", subtype="PullRequest",
                   title="Fix bug", repo="example/repo", updated="2026-04-15T00:00:00Z",
                   unread="true", subject_url="https://api.github.com/repos/example/repo/pulls/100"):
        return "\t".join([nid, reason, subtype, title, repo, updated, unread, subject_url])

    def test_basic_comment(self):
        line = self._make_line()
        now = time.time()
        result = _parse_notification_line(line, now)
        assert result is not None
        assert result["id"] == "123"
        assert result["type"] == "comment"
        assert result["label"] == "New comment"
        assert result["subject"] == "PullRequest"
        assert result["title"] == "Fix bug"
        assert result["repo"] == "example/repo"
        assert result["pr_number"] == 100
        assert result["unread"] is True

    def test_review_requested(self):
        line = self._make_line(reason="review_requested")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "review_requested"
        assert result["label"] == "Review requested"

    def test_ci_activity(self):
        line = self._make_line(reason="ci_activity", title="CI failed for feature branch",
                               subject_url="")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "ci_activity"
        assert result["label"] == "CI update"

    def test_mention(self):
        line = self._make_line(reason="mention")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "mention"
        assert result["label"] == "Mentioned"

    def test_assign(self):
        line = self._make_line(reason="assign")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "assign"
        assert result["label"] == "Assigned"

    def test_state_change(self):
        line = self._make_line(reason="state_change")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "state_change"
        assert result["label"] == "State changed"

    def test_author(self):
        line = self._make_line(reason="author")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "author"
        assert result["label"] == "PR update"

    def test_subscribed(self):
        line = self._make_line(reason="subscribed")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["type"] == "subscribed"
        assert result["label"] == "Subscribed"

    def test_unknown_reason_uses_raw(self):
        line = self._make_line(reason="future_type")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["label"] == "future_type"

    def test_pr_number_from_subject_url(self):
        url = "https://api.github.com/repos/example/repo/pulls/555"
        line = self._make_line(subject_url=url)
        result = _parse_notification_line(line, time.time())
        assert result["pr_number"] == 555

    def test_no_pr_number_when_no_pulls_in_url(self):
        url = "https://api.github.com/repos/example/repo/issues/42"
        line = self._make_line(subject_url=url)
        result = _parse_notification_line(line, time.time())
        assert result["pr_number"] is None

    def test_no_subject_url(self):
        line = self._make_line(subject_url="")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["pr_number"] is None

    def test_unread_false(self):
        line = self._make_line(unread="false")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["unread"] is False

    def test_too_few_fields_returns_none(self):
        line = "id\treason\ttype\ttitle\trepo"
        result = _parse_notification_line(line, time.time())
        assert result is None

    def test_short_line_six_fields_returns_none(self):
        line = "id\treason\ttype\ttitle\trepo\t2026-01-01"
        result = _parse_notification_line(line, time.time())
        assert result is None

    def test_exactly_seven_fields_no_subject_url(self):
        line = "id\tcomment\tPullRequest\tTitle\texample/repo\t2026-01-01\ttrue"
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["pr_number"] is None

    def test_repo_not_allowed_returns_none(self):
        line = self._make_line(repo="random-org/random-repo")
        result = _parse_notification_line(line, time.time())
        assert result is None

    def test_fork_repo_allowed(self):
        line = self._make_line(repo="test-author/repo")
        result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["repo"] == "test-author/repo"

    def test_company_org_allowed(self):
        line = self._make_line(repo="myorg/monorepo")
        result = _parse_notification_line(line, time.time())
        assert result is not None

    def test_ci_activity_branch_lookup(self):
        """ci_activity with 'for <branch> branch' pattern triggers branch lookup."""
        line = self._make_line(
            reason="ci_activity",
            title="CI failed for my-feature branch",
            subject_url="",
        )
        with patch("services.github_poller._lookup_pr_by_branch", return_value=42):
            result = _parse_notification_line(line, time.time())
        assert result is not None
        assert result["pr_number"] == 42
        assert result["branch"] == "my-feature"

    def test_ci_activity_main_branch_skipped(self):
        """ci_activity for 'main' or 'master' branch returns None."""
        line = self._make_line(
            reason="ci_activity",
            title="CI passed for main branch",
            subject_url="",
        )
        result = _parse_notification_line(line, time.time())
        assert result is None

    def test_ci_activity_master_branch_skipped(self):
        line = self._make_line(
            reason="ci_activity",
            title="CI passed for master branch",
            subject_url="",
        )
        result = _parse_notification_line(line, time.time())
        assert result is None


class TestMarkReadById:
    """POST /api/events/read with specific ids."""

    def test_mark_by_ids(self, events_client):
        import server
        server.emit_event("test.a", {"title": "A", "message": "first"})
        server.emit_event("test.b", {"title": "B", "message": "second"})

        resp = events_client.get("/api/events")
        all_events = resp.json()["events"]
        assert len(all_events) >= 2
        target_id = all_events[0]["id"]

        resp_mark = events_client.post("/api/events/read", json={"ids": [target_id]})
        assert resp_mark.status_code == 200

        resp_after = events_client.get("/api/events")
        data = resp_after.json()
        marked = [e for e in data["events"] if e["id"] == target_id]
        assert len(marked) == 1
        assert marked[0]["read"] == 1


@pytest.fixture
def events_client(tmp_path, monkeypatch):
    """Client with temp DBs for event tests."""
    import server
    from eva_db import EvaDB

    test_db = EvaDB(str(tmp_path / "eva.db"))
    monkeypatch.setattr("server._db", test_db)

    import yaml
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump({"projects": {"test-proj": {"name": "Test"}}}, f)
    monkeypatch.setattr("server.CONFIG_PATH", config_path)

    from starlette.testclient import TestClient
    yield TestClient(server.app)
    test_db.close()
