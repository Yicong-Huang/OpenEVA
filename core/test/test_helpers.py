"""Unit tests for helper functions: config, tasks, project stats, blocking, status suggestions."""

import os
import sys
import yaml
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---- Config helpers ----

class TestLoadConfig:
    def test_load_config_reads_yaml(self, patched_server):
        config = patched_server.load_config()
        assert "projects" in config
        assert "test-proj" in config["projects"]
        assert config["projects"]["test-proj"]["name"] == "Test Project"

    def test_load_config_has_all_projects(self, patched_server):
        config = patched_server.load_config()
        assert len(config["projects"]) == 2
        assert "empty-proj" in config["projects"]


class TestSaveConfig:
    def test_save_config_persists(self, patched_server):
        config = patched_server.load_config()
        config["projects"]["new-proj"] = {
            "name": "New Project",
            "description": "Added by test",
        }
        patched_server.save_config(config)

        reloaded = patched_server.load_config()
        assert "new-proj" in reloaded["projects"]
        assert reloaded["projects"]["new-proj"]["name"] == "New Project"

    def test_save_config_preserves_existing(self, patched_server):
        config = patched_server.load_config()
        config["extra_key"] = "extra_value"
        patched_server.save_config(config)

        reloaded = patched_server.load_config()
        assert reloaded["extra_key"] == "extra_value"
        assert "test-proj" in reloaded["projects"]


# ---- Task helpers ----

class TestLoadTasks:
    def test_load_tasks_returns_all(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        assert len(tasks) == 4
        assert "task-a" in tasks
        assert "task-b" in tasks
        assert "task-c" in tasks
        assert "task-d" in tasks

    def test_load_tasks_empty_project(self, patched_server):
        tasks = patched_server.load_tasks("empty-proj")
        assert tasks == {}

    def test_load_tasks_nonexistent_project(self, patched_server):
        tasks = patched_server.load_tasks("does-not-exist")
        assert tasks == {}

    def test_load_tasks_has_correct_fields(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        task_a = tasks["task-a"]
        assert task_a["description"] == "Task A - foundation work"
        assert task_a["type"] == "feature"
        assert task_a["status"] == "done"
        assert task_a["group"] == "core"
        assert task_a["dependencies"] == []

    def test_load_tasks_no_extra_task_ids(self, patched_server):
        """Tasks loaded from DB should only contain what was inserted."""
        tasks = patched_server.load_tasks("test-proj")
        assert "sessions" not in tasks
        assert len(tasks) == 4


class TestSaveTask:
    def test_save_task_persists(self, patched_server):
        """save_task should write to SQLite and be retrievable via load_tasks."""
        patched_server.save_task("test-proj", "new-task", {
            "description": "A new task",
            "type": "feature",
            "status": "not_started",
        })
        tasks = patched_server.load_tasks("test-proj")
        assert "new-task" in tasks
        assert tasks["new-task"]["description"] == "A new task"
        assert "updated_at" in tasks["new-task"]

    def test_save_task_updates_existing(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        task_a = tasks["task-a"]
        task_a["notes"] = "Test note"
        patched_server.save_task("test-proj", "task-a", task_a)

        reloaded = patched_server.load_tasks("test-proj")
        assert reloaded["task-a"]["notes"] == "Test note"
        assert "updated_at" in reloaded["task-a"]

    def test_save_task_creates_new_project_entry(self, patched_server):
        """save_task for a new project should create the task in the DB."""
        patched_server.save_task("brand-new-proj", "first-task", {
            "description": "First task",
            "type": "feature",
            "status": "not_started",
        })
        tasks = patched_server.load_tasks("brand-new-proj")
        assert "first-task" in tasks
        assert tasks["first-task"]["description"] == "First task"


# ---- Project stats ----

class TestComputeProjectStats:
    def test_counts_statuses(self, patched_server):
        stats = patched_server.compute_project_stats("test-proj")
        assert stats["total"] == 4
        assert stats["counts"]["done"] == 1
        assert stats["counts"]["in_progress"] == 1
        # task-c and task-d are stored as not_started in DB
        # blocked is computed separately, not stored in DB
        assert stats["counts"]["not_started"] == 2

    def test_progress_percentage(self, patched_server):
        stats = patched_server.compute_project_stats("test-proj")
        # 1 done out of 4 total = 25.0%
        assert stats["progress"] == 25.0

    def test_empty_project_stats(self, patched_server):
        stats = patched_server.compute_project_stats("empty-proj")
        assert stats["total"] == 0
        assert stats["progress"] == 0.0


# ---- Blocking logic ----

class TestIsTaskBlocked:
    def test_no_dependencies_not_blocked(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        assert patched_server.is_task_blocked("task-a", tasks) is False

    def test_done_dependency_not_blocked(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        # task-b depends on task-a which is done
        assert patched_server.is_task_blocked("task-b", tasks) is False

    def test_incomplete_dependency_blocked(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        # task-c depends on task-b which is in_progress
        assert patched_server.is_task_blocked("task-c", tasks) is True

    def test_nonexistent_dependency_blocked(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        # Add a task with a dependency that does not exist
        tasks["task-phantom"] = {
            "description": "Phantom",
            "dependencies": ["nonexistent"],
            "status": "not_started",
        }
        assert patched_server.is_task_blocked("task-phantom", tasks) is True

    def test_nonexistent_task_not_blocked(self, patched_server):
        tasks = patched_server.load_tasks("test-proj")
        # A task not in the dict -> no dependencies -> not blocked
        assert patched_server.is_task_blocked("does-not-exist", tasks) is False


# ---- Status suggestion ----

class TestSuggestTaskStatus:
    def test_all_prs_merged_suggests_done(self, patched_server):
        task = {
            "status": "in_review",
            "prs": [{"status": "merged"}, {"status": "merged"}],
        }
        assert patched_server.suggest_task_status(task) == "done"

    def test_open_pr_suggests_in_review(self, patched_server):
        task = {
            "status": "in_progress",
            "prs": [{"status": "open"}],
        }
        assert patched_server.suggest_task_status(task) == "in_review"

    def test_draft_pr_suggests_in_review(self, patched_server):
        task = {
            "status": "not_started",
            "prs": [{"status": "draft"}],
        }
        assert patched_server.suggest_task_status(task) == "in_review"

    def test_ticket_suggests_in_progress(self, patched_server):
        task = {
            "status": "not_started",
            "ticket": {"id": "EX-123"},
            "prs": [],
        }
        assert patched_server.suggest_task_status(task) == "in_progress"

    def test_no_suggestion_when_correct(self, patched_server):
        task = {
            "status": "done",
            "prs": [{"status": "merged"}],
        }
        assert patched_server.suggest_task_status(task) is None

    def test_no_suggestion_for_bare_task(self, patched_server):
        task = {
            "status": "not_started",
            "prs": [],
        }
        assert patched_server.suggest_task_status(task) is None

    def test_already_in_review_with_open_pr(self, patched_server):
        task = {
            "status": "in_review",
            "prs": [{"status": "open"}],
        }
        # Already in_review, no change suggested
        assert patched_server.suggest_task_status(task) is None


# ---- Enrich project ----

class TestEnrichProject:
    def test_enrich_adds_computed_fields(self, patched_server):
        config = patched_server.load_config()
        proj = config["projects"]["test-proj"]
        enriched = patched_server.enrich_project("test-proj", proj)
        assert enriched["id"] == "test-proj"
        assert "tasks" in enriched
        assert "progress" in enriched
        assert "task_counts" in enriched
        assert enriched["progress"] == 25.0

    def test_enrich_follow_ups_default_empty(self, patched_server):
        """follow_ups defaults to empty list when none are set in the DB."""
        config = patched_server.load_config()
        proj = config["projects"]["test-proj"]
        enriched = patched_server.enrich_project("test-proj", proj)
        tasks = enriched["tasks"]
        assert tasks["task-a"]["follow_ups"] == []
        assert tasks["task-b"]["follow_ups"] == []

    def test_enrich_follow_ups_from_db(self, patched_server):
        """follow_ups set via update_task appear in enriched project."""
        patched_server._db.update_task(
            "test-proj", "task-a",
            follow_ups=["rebase on latest master", "fix type hints"],
        )
        config = patched_server.load_config()
        proj = config["projects"]["test-proj"]
        enriched = patched_server.enrich_project("test-proj", proj)
        tasks = enriched["tasks"]
        assert tasks["task-a"]["follow_ups"] == ["rebase on latest master", "fix type hints"]


# ---- is_repo_allowed ----

class TestIsRepoAllowed:
    """Test the allow-list logic against a generic, monkeypatched
    config so the behaviour is decoupled from whatever repos the
    maintainer happens to track. An open-source fork has its own
    ALLOWED_REPOS / FORK_TO_UPSTREAM from settings."""

    def _patch_config(self, monkeypatch, *, allowed, forks):
        from adapters import github as gh
        monkeypatch.setattr(gh, "ALLOWED_REPOS", set(allowed))
        monkeypatch.setattr(gh, "FORK_TO_UPSTREAM", dict(forks))
        # ALLOWED_ORGS is derived from ALLOWED_REPOS at import time;
        # rebuild so the wildcard branch picks up the new config.
        monkeypatch.setattr(
            gh, "ALLOWED_ORGS",
            {r.split("/")[0] for r in allowed if r.endswith("/*")},
        )

    def test_is_repo_allowed_fork(self, monkeypatch):
        from _test_constants import (
            TEST_OSS_REPO, TEST_OSS_FORK,
        )
        self._patch_config(
            monkeypatch,
            allowed=[TEST_OSS_REPO],
            forks={TEST_OSS_FORK: TEST_OSS_REPO},
        )
        import server
        # alice/widgets resolves to acme/widgets via FORK_TO_UPSTREAM
        # and acme/widgets is in ALLOWED_REPOS -> allowed.
        assert server.is_repo_allowed(TEST_OSS_FORK) is True

    def test_is_repo_allowed_org(self, monkeypatch):
        from _test_constants import (
            TEST_COMPANY_ORG, TEST_COMPANY_REPO_RUNTIME,
        )
        self._patch_config(
            monkeypatch,
            allowed=[f"{TEST_COMPANY_ORG}/*"],
            forks={},
        )
        import server
        assert server.is_repo_allowed(TEST_COMPANY_REPO_RUNTIME) is True

    def test_is_repo_allowed_rejected(self, monkeypatch):
        self._patch_config(
            monkeypatch, allowed=["acme/widgets"], forks={},
        )
        import server
        assert server.is_repo_allowed("random-user/random-repo") is False

    def test_is_repo_allowed_empty_string(self):
        import server
        assert server.is_repo_allowed("") is False


# ---- Config cache ----

class TestConfigCache:
    def test_load_config_returns_dict(self, patched_server):
        """load_config should return a dict with projects key."""
        from server import load_config, _config_cache
        _config_cache["data"] = None
        _config_cache["mtime"] = 0
        config = load_config()
        assert isinstance(config, dict)
        assert "projects" in config

    def test_load_config_cached(self, patched_server):
        """Repeated calls should return same object (cached)."""
        from server import load_config, _config_cache
        _config_cache["data"] = None
        _config_cache["mtime"] = 0
        c1 = load_config()
        c2 = load_config()
        assert c1 is c2  # same object = cached

    def test_load_config_invalidated_on_save(self, patched_server):
        """save_config should update the cache."""
        from server import load_config, save_config, _config_cache
        _config_cache["data"] = None
        _config_cache["mtime"] = 0
        c1 = load_config()
        c1_copy = dict(c1)
        c1_copy["test_key"] = "test_value"
        save_config(c1_copy)
        c2 = load_config()
        assert c2.get("test_key") == "test_value"


# ---- PR info cache ----

class TestPrInfoCacheSize:
    def test_pr_info_cache_starts_empty(self):
        """_pr_info_cache should be a dict."""
        from server import _pr_info_cache
        assert isinstance(_pr_info_cache, dict)


# ---- suggest_task_status (direct import, no fixture) ----

class TestSuggestTaskStatusDirect:
    def test_suggest_task_status_all_merged(self):
        """All PRs merged -> suggest done."""
        import server
        task = {
            "status": "in_review",
            "prs": [{"status": "merged"}, {"status": "merged"}, {"status": "merged"}],
        }
        assert server.suggest_task_status(task) == "done"

    def test_suggest_task_status_has_open_pr(self):
        """An open PR and current status not in_review -> suggest in_review."""
        import server
        task = {
            "status": "not_started",
            "prs": [{"status": "open"}],
        }
        assert server.suggest_task_status(task) == "in_review"

    def test_suggest_task_status_no_change(self):
        """No PRs and no ticket -> return None (no suggestion)."""
        import server
        task = {
            "status": "not_started",
            "prs": [],
        }
        assert server.suggest_task_status(task) is None
