"""Tests for pr_sync.py module (pure functions, no server dependency)."""

import common
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pr_sync import (
    aggregate_ci_status,
    extract_ticket,
    match_pr_to_task,
    strip_title_tags,
    ticket_url,
)
from eva_db import EvaDB as TaskDB


class TestExtractTicket:
    def test_ex_ticket(self):
        assert extract_ticket("[EX-56253][PYTHON] Fix something") == "EX-56253"

    def test_sc_ticket(self):
        assert extract_ticket("[ALT-2001] Query events") == "ALT-2001"

    def test_xta_ticket(self):
        assert extract_ticket("[ZOO-3001] Function tagging") == "ZOO-3001"

    def test_no_ticket(self):
        assert extract_ticket("Fix a random bug") is None

    def test_empty(self):
        assert extract_ticket("") is None

    def test_multiple_tags_returns_first(self):
        assert extract_ticket("[EX-111][EX-222] Test") == "EX-111"


class TestStripTitleTags:
    def test_single_tag(self):
        assert strip_title_tags("[EX-123] Fix bug") == "Fix bug"

    def test_multiple_tags(self):
        assert strip_title_tags("[EX-123][PYTHON][DOCS] Fix docs") == "Fix docs"

    def test_no_tags(self):
        assert strip_title_tags("Just a title") == "Just a title"

    def test_empty(self):
        assert strip_title_tags("") == ""

    def test_tags_only(self):
        assert strip_title_tags("[EX-123]") == ""


class TestTicketUrl:
    def test_ex(self):
        assert ticket_url("EX-55390") == "https://issues.example.org/jira/browse/EX-55390"

    def test_sc(self):
        assert ticket_url("ALT-2001") == "https://example.atlassian.net/browse/ALT-2001"

    def test_xta(self):
        assert ticket_url("ZOO-3001") == "https://example.atlassian.net/browse/ZOO-3001"

    def test_unknown_prefix(self):
        assert ticket_url("UNKNOWN-123") == ""


class TestAggregateCiStatusModule:
    """Test aggregate_ci_status from pr_sync module directly."""

    def test_none_input(self):
        assert aggregate_ci_status(None) == "unknown"

    def test_single_success(self):
        assert aggregate_ci_status([{"conclusion": "SUCCESS"}]) == "success"

    def test_mixed_case(self):
        assert aggregate_ci_status([{"conclusion": "success"}, {"conclusion": "SUCCESS"}]) == "success"

    def test_failure_wins(self):
        assert aggregate_ci_status([{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}]) == "failure"


# ---------------------------------------------------------------------------
# Integration tests using a real temp TaskDB
# ---------------------------------------------------------------------------

@pytest.fixture()
def temp_task_db(tmp_path):
    """Create a real TaskDB in a temp file."""
    db = TaskDB(str(tmp_path / "tasks.db"))
    yield db
    db.close()


class TestMatchPrToTaskWithDb:
    """match_pr_to_task against a real TaskDB fixture."""

    def test_match_pr_to_task_with_db(self, temp_task_db):
        """Create a task with a ticket, verify match_pr_to_task finds it."""
        temp_task_db.create_task(
            project="oss-repo",
            task_id="repo-56253",
            description="Fix something",
            ticket_id="EX-56253",
            ticket_url="https://issues.example.org/jira/browse/EX-56253",
        )
        result = match_pr_to_task(
            "[EX-56253][PYTHON] Fix something",
            temp_task_db,
        )
        assert result is not None
        project, task_id = result
        assert project == "oss-repo"
        assert task_id == "repo-56253"

    def test_match_pr_to_task_missing_ticket_returns_none(self, temp_task_db):
        """No task for the ticket -> None (no auto-create)."""
        result = match_pr_to_task(
            "[EX-99001][SQL] Brand new fix",
            temp_task_db,
        )
        assert result is None

    def test_match_pr_to_task_no_ticket_returns_none(self, temp_task_db):
        """A PR title without brackets returns None."""
        result = match_pr_to_task(
            "Fix typo in README",
            temp_task_db,
        )
        assert result is None
