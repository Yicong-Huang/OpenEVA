"""Tests for the eva-cli bash CLI tool.

Tests CLI command functions directly by importing them via SourceFileLoader
and calling them with mock args against a temp EvaDB.
"""

import argparse
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import warnings
from importlib.machinery import SourceFileLoader
from unittest.mock import patch, MagicMock

import pytest

_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", ".."),
)
sys.path.insert(0, _REPO_ROOT)

EVA_CLI = os.path.join(_REPO_ROOT, "eva-cli")
# Tests that subprocess.run() the CLI must invoke it with the same
# interpreter the test suite is running under -- the script's shebang
# is `#!/usr/bin/env python3` which on a fresh OSS machine resolves
# to the system python (no FastAPI, no Eva deps installed). Using
# `sys.executable` guarantees we hit the venv that ran pytest.
EVA_CLI_ARGV0 = [sys.executable, EVA_CLI]

# Load CLI module without running main()
with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)
    cli_mod = SourceFileLoader("eva_cli", EVA_CLI).load_module()


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture()
def temp_db():
    """Create a temp EvaDB and patch app_state._db."""
    from eva_db import EvaDB
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = EvaDB(path)

    # Seed a project and some tasks
    db.create_project("proj-a", name="Project A", description="Test project")
    db.create_project("proj-b", name="Project B")
    db.create_task("proj-a", "task-1", description="First task", status="not_started")
    db.create_task("proj-a", "task-2", description="Second task", status="in_progress")
    db.create_task("proj-a", "task-3", description="Third task", status="done")
    db.add_dependency("proj-a", "task-2", "task-1")

    import server

    orig_db = server._db
    server._db = db

    # Force re-init of lazy-loaded modules so the patched _db is picked up
    cli_mod._core_loaded = False
    cli_mod._ensure_core()

    yield db

    server._db = orig_db
    db.close()
    os.unlink(path)


def _make_args(**kwargs):
    """Build a namespace that behaves like argparse args with defaults."""
    defaults = {"json": False, "base_url": "http://localhost:8021"}
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ============================================================
# Subprocess tests (help, flags, error handling)
# ============================================================


def test_eva_cli_help():
    """eva-cli help should print usage and exit 0."""
    r = subprocess.run([*EVA_CLI_ARGV0, "help"], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "eva" in r.stdout.lower()
    assert "update-task" in r.stdout
    assert "get-task" in r.stdout
    assert "list-tasks" in r.stdout
    assert "fetch-tasks" in r.stdout
    assert "rename-task" in r.stdout


def test_eva_cli_trailing_json_flag_works():
    """Memo item #7: `--json` should work whether placed before OR
    after the subcommand. Regression: argparse only honoured the
    flag at top-level by default; `eva-cli list-projects --json`
    used to fail with 'unrecognized arguments: --json'. Hoist
    logic in `main()` normalises both placements."""
    import json as _json
    # Trailing placement (the one that used to break).
    r = subprocess.run([*EVA_CLI_ARGV0, "list-projects", "--json"],
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 0, r.stderr
    data = _json.loads(r.stdout)
    assert isinstance(data, list)
    # Leading placement (always worked) -- both must yield the same.
    r2 = subprocess.run([*EVA_CLI_ARGV0, "--json", "list-projects"],
                        capture_output=True, text=True, timeout=5)
    assert r2.returncode == 0, r2.stderr
    data2 = _json.loads(r2.stdout)
    assert data == data2


def test_eva_cli_audit_json_works_at_either_position():
    """Audit had a per-subcommand `--json` declaration with
    `default=False` that silently clobbered the top-level value.
    Now uses `default=argparse.SUPPRESS` so both placements yield
    the same JSON envelope."""
    import json as _json
    # Trailing
    r = subprocess.run([*EVA_CLI_ARGV0, "audit", "--json"],
                       capture_output=True, text=True, timeout=15)
    # Audit exits 0 (no errors) or 2 (errors found) -- both valid;
    # we just need the JSON envelope.
    assert r.returncode in (0, 2), r.stderr
    data = _json.loads(r.stdout)
    assert "findings" in data and "summary" in data
    # Leading
    r2 = subprocess.run([*EVA_CLI_ARGV0, "--json", "audit"],
                        capture_output=True, text=True, timeout=15)
    assert r2.returncode in (0, 2), r2.stderr
    data2 = _json.loads(r2.stdout)
    assert "findings" in data2 and "summary" in data2


def test_eva_cli_missing_deps_error_lists_three_options():
    """OSS-readiness regression: when fastapi/pydantic aren't on the
    import path, the user used to see a cryptic ImportError. The
    ensure-core helper now prints three concrete remediations: create
    .venv, set EVA_VENV, or invoke with the venv's python directly.

    Hard to trigger the live ImportError in-process (core is already
    loaded by the test runner), so we instead inject a fake
    ImportError into the import inside `_ensure_core` and assert the
    error-text contract. That keeps the test fast and CI-safe while
    locking the user-facing strings."""
    cli_mod._core_loaded = False
    err_buf = io.StringIO()

    real_import = __import__

    def fake_import(name, *_a, **_k):
        if name == "common":
            raise ImportError("simulated: fastapi not on path")
        return real_import(name, *_a, **_k)

    with patch("builtins.__import__", side_effect=fake_import), \
         patch.object(sys, "exit") as mock_exit, \
         patch.object(sys, "stderr", err_buf):
        cli_mod._ensure_core()

    mock_exit.assert_called_once_with(1)
    err = err_buf.getvalue()
    # The three remediations the user sees.
    assert "python -m venv .venv" in err, err
    assert "EVA_VENV=" in err, err
    assert ".venv/bin/python eva-cli" in err, err
    # The original ImportError text is also surfaced so a debug-savvy
    # user can see WHAT was missing, not just a generic suggestion.
    assert "simulated: fastapi not on path" in err, err
    cli_mod._core_loaded = False  # reset for downstream tests


def test_eva_cli_help_documents_json_and_error_conventions():
    """The top-level description must tell users about --json and error format
    so agent knows what to parse without reading source."""
    r = subprocess.run([*EVA_CLI_ARGV0, "--help"], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "--json" in r.stdout
    assert "Error:" in r.stdout
    assert "stderr" in r.stdout


def test_eva_cli_help_distinguishes_list_tasks_vs_fetch_tasks():
    """Agent users get confused by the two commands; the help text must make
    the difference obvious (compact row vs multi-line dump)."""
    r = subprocess.run([*EVA_CLI_ARGV0, "--help"], capture_output=True, text=True, timeout=5)
    assert "compact" in r.stdout
    assert "multi-line" in r.stdout


def test_eva_cli_help_no_stale_25_commands_claim():
    """Regression: the description used to hard-code '25 commands' while 29
    actually exist; never allow that kind of drift back in."""
    r = subprocess.run([*EVA_CLI_ARGV0, "--help"], capture_output=True, text=True, timeout=5)
    assert "25 commands" not in r.stdout


def test_eva_cli_audit_help_advertises_flags():
    """`audit --help` must mention --json, --fix, and --kind so users
    discover the structured / repair / filter modes without grepping
    source."""
    r = subprocess.run([*EVA_CLI_ARGV0, "audit", "--help"],
                       capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "--json" in r.stdout
    assert "--fix" in r.stdout
    assert "--kind" in r.stdout


def test_eva_cli_audit_runs_clean_against_real_db():
    """Smoke: `eva-cli audit --json` runs end-to-end and emits a
    summary block. Doesn't assert on findings count (depends on local
    DB state) but does require the JSON envelope shape."""
    import json as _json
    r = subprocess.run([*EVA_CLI_ARGV0, "audit", "--json"],
                       capture_output=True, text=True, timeout=15)
    # Exit code is 0 (no errors) or 2 (errors found) -- both are valid
    # smoke results. 1 would mean an unhandled exception.
    assert r.returncode in (0, 2), f"audit crashed: {r.stderr[:200]}"
    body = _json.loads(r.stdout)
    assert "findings" in body
    assert "summary" in body
    assert "total" in body["summary"]


def test_eva_cli_unknown_command():
    """Unknown command should exit 1."""
    r = subprocess.run([*EVA_CLI_ARGV0, "nonexistent"], capture_output=True, text=True, timeout=5)
    assert r.returncode == 1
    assert "Unknown command" in r.stderr


def test_eva_cli_project_flag_hint():
    """`--project FOO` is a common confusion -- project + task_id are
    positional. The error must point users in the right direction
    rather than just echoing argparse's terse 'unrecognized arguments'."""
    r = subprocess.run(
        [*EVA_CLI_ARGV0, "create-task", "--project", "p", "--task-id", "t",
         "--description", "x"],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr
    assert "FIRST positional arg" in r.stderr
    assert "SECOND positional arg" in r.stderr
    assert "<cmd> --help" in r.stderr


def test_eva_cli_unrelated_unrecognized_arg_falls_through():
    """The new hint only fires for project/task flags. An unrelated
    typo like `--bogus` should still get the default argparse error
    so the user gets argparse's exact diagnostics."""
    r = subprocess.run(
        [*EVA_CLI_ARGV0, "list-tasks", "test-proj", "--bogus", "x"],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr
    # No positional-arg hint -- this isn't a project/task flag confusion.
    assert "FIRST positional arg" not in r.stderr


def test_eva_cli_extra_bare_positional_suggests_flag():
    """Common confusion: user types `create-task PROJ TID 'description text'`
    expecting `description` as a third positional. argparse rejects
    `'description text'` as unrecognized; the hint must point at the
    flag form (`--description`)."""
    r = subprocess.run(
        [*EVA_CLI_ARGV0, "create-task", "p", "t", "first task description"],
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 2
    assert "unrecognized arguments" in r.stderr
    # Hint mentions the flag form so the user knows the right shape.
    assert "--description" in r.stderr
    assert "extra positional value(s)" in r.stderr

def test_eva_cli_help_flag():
    """--help flag should print usage."""
    r = subprocess.run([*EVA_CLI_ARGV0, "--help"], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "update-task" in r.stdout


def test_eva_cli_h_flag():
    """-h flag should print usage."""
    r = subprocess.run([*EVA_CLI_ARGV0, "-h"], capture_output=True, text=True, timeout=5)
    assert r.returncode == 0
    assert "update-task" in r.stdout


# ============================================================
# list-projects
# ============================================================


def test_cmd_list_projects_text(temp_db, capsys):
    """list-projects prints a table with project names."""
    cli_mod._ensure_core()
    args = _make_args()
    cli_mod.cmd_list_projects(args)
    out = capsys.readouterr().out
    assert "proj-a" in out or "Project A" in out


def test_cmd_list_projects_json(temp_db, capsys):
    """list-projects --json outputs valid JSON list."""
    cli_mod._ensure_core()
    args = _make_args(json=True)
    cli_mod.cmd_list_projects(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    ids = [p["id"] for p in data]
    assert "proj-a" in ids
    assert "proj-b" in ids


def test_cmd_list_projects_empty(capsys):
    """list-projects with no projects prints a dim message."""
    from eva_db import EvaDB
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db = EvaDB(path)

    import server
    orig = server._db
    server._db = db

    cli_mod._ensure_core()
    args = _make_args()
    cli_mod.cmd_list_projects(args)
    out = capsys.readouterr().out
    assert "No projects" in out or out.strip() == ""

    server._db = orig
    db.close()
    os.unlink(path)


# ============================================================
# list-tasks
# ============================================================


def test_cmd_list_tasks_valid_project(temp_db, capsys):
    """list-tasks on a valid project shows tasks."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a")
    cli_mod.cmd_list_tasks(args)
    out = capsys.readouterr().out
    assert "task-1" in out
    assert "task-2" in out


def test_cmd_list_tasks_json(temp_db, capsys):
    """list-tasks --json outputs a dict of tasks."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", json=True)
    cli_mod.cmd_list_tasks(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, dict)
    assert "task-1" in data


def test_cmd_list_tasks_invalid_project(temp_db):
    """list-tasks on a nonexistent project exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="no-such-proj")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_list_tasks(args)
    assert exc_info.value.code == 1


def test_cmd_list_tasks_empty_project(temp_db, capsys):
    """list-tasks on a project with no tasks prints a dim message."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-b")
    cli_mod.cmd_list_tasks(args)
    out = capsys.readouterr().out
    assert "No tasks" in out


# ============================================================
# get-task
# ============================================================


def test_cmd_get_task_found(temp_db, capsys):
    """get-task with valid IDs prints task info."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1")
    cli_mod.cmd_get_task(args)
    out = capsys.readouterr().out
    assert "task-1" in out
    assert "First task" in out


def test_cmd_get_task_json(temp_db, capsys):
    """get-task --json outputs valid JSON with task fields."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] == "task-1"
    assert data["description"] == "First task"


def test_cmd_get_task_not_found(temp_db):
    """get-task with nonexistent task exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="no-task")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_get_task(args)
    assert exc_info.value.code == 1


# ============================================================
# create-task then get-task roundtrip
# ============================================================


def test_create_then_get_task(temp_db, capsys):
    """create-task + get-task roundtrip verifies the task was stored.
    `--type bug` is normalised to canonical `fix` at the boundary;
    same alias behaviour as `feat`->`feature` and `doc`->`docs`."""
    cli_mod._ensure_core()
    create_args = _make_args(
        project="proj-a", task_id="new-task",
        description="A newly created task",
        type="bug", status="not_started", group="g1",
    )
    cli_mod.cmd_create_task(create_args)
    capsys.readouterr()  # clear

    get_args = _make_args(project="proj-a", task_id="new-task", json=True)
    cli_mod.cmd_get_task(get_args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["id"] == "new-task"
    assert data["description"] == "A newly created task"
    # `bug` -> canonical `fix`
    assert data["type"] == "fix"


def test_create_task_duplicate(temp_db):
    """create-task with existing ID exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        description="Dup", type="feature",
        status="not_started", group="",
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_create_task(args)
    assert exc_info.value.code == 1


def test_create_task_invalid_project(temp_db):
    """create-task on nonexistent project exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="ghost-proj", task_id="x",
        description="", type="feature",
        status="not_started", group="",
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_create_task(args)
    assert exc_info.value.code == 1


# ============================================================
# update-task
# ============================================================


def test_cmd_update_task_status(temp_db, capsys):
    """update-task --status changes the task status."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status="in_progress", description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    out = capsys.readouterr().out
    assert "Updated" in out

    # Verify via get-task
    get_args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "in_progress"


def test_cmd_update_task_description(temp_db, capsys):
    """update-task --description changes the description."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description="Updated desc", notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    capsys.readouterr()

    get_args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert data["description"] == "Updated desc"


def test_cmd_update_task_notes(temp_db, capsys):
    """update-task --notes sets notes."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes="Some notes here",
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    capsys.readouterr()

    get_args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert data["notes"] == "Some notes here"


def test_cmd_update_task_ticket_id(temp_db, capsys):
    """update-task --ticket-id sets the ticket."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id="EX-1234", ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    capsys.readouterr()

    get_args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert data.get("ticket_id") == "EX-1234"


def test_cmd_update_task_no_fields(temp_db):
    """update-task with no fields exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_update_task(args)
    assert exc_info.value.code == 1


def test_cmd_update_task_not_found(temp_db):
    """update-task on nonexistent task exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="ghost",
        status="done", description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_update_task(args)
    assert exc_info.value.code == 1


def test_cmd_update_task_json(temp_db, capsys):
    """update-task --json outputs updated task as JSON."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status="done", description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
        json=True,
    )
    cli_mod.cmd_update_task(args)
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "done"


def test_cmd_update_task_follow_ups_persists(temp_db, capsys):
    """Regression: `--follow-up X` (via argparse action='append') must actually
    end up in the DB. Previously `app_state.save_task`'s field whitelist dropped
    follow_ups silently, so the CLI appeared to succeed but the DB never changed.
    """
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None,
        follow_ups=["rebase on master", "update docs"],
    )
    cli_mod.cmd_update_task(args)
    # Read back through EvaDB to verify persistence.
    task = temp_db.get_task("proj-a", "task-1")
    assert task["follow_ups"] == ["rebase on master", "update docs"]


def test_cmd_update_task_priority_persists(temp_db, capsys):
    """`--priority N` must reach the DB. Covers the priority branch
    of the field-mapper that was previously untested."""
    cli_mod._ensure_core()
    cli_mod.cmd_update_task(_make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=5, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    ))
    task = temp_db.get_task("proj-a", "task-1")
    assert task["priority"] == 5


def test_cmd_update_task_type_persists(temp_db, capsys):
    """`--type bug` flips the task's type column. The boundary in
    common.tasks.update_task canonicalises 'bug' -> 'fix' so the
    persisted value lands on the canonical spelling."""
    cli_mod._ensure_core()
    cli_mod.cmd_update_task(_make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type="bug", group=None, follow_ups=None,
    ))
    task = temp_db.get_task("proj-a", "task-1")
    # `bug` -> canonical `fix`
    assert task["type"] == "fix"


def test_cmd_update_task_group_writes_to_group_name_column(temp_db, capsys):
    """The CLI flag is `--group` but the DB column is `group_name`
    (avoiding the SQL keyword). The mapper must translate. Covers the
    branch + the rename."""
    cli_mod._ensure_core()
    cli_mod.cmd_update_task(_make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group="performance", follow_ups=None,
    ))
    task = temp_db.get_task("proj-a", "task-1")
    assert task["group_name"] == "performance"


def test_cmd_update_task_empty_string_clears_notes(temp_db, capsys):
    """Regression: CLI used to skip empty-string args via truthy check,
    so `--notes ""` was silently ignored. Now empty strings clear the field."""
    cli_mod._ensure_core()
    # Seed with a non-empty notes value.
    temp_db.update_task("proj-a", "task-1", notes="old note")
    assert temp_db.get_task("proj-a", "task-1")["notes"] == "old note"

    # Pass empty string explicitly -> should clear.
    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes="",
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    assert temp_db.get_task("proj-a", "task-1")["notes"] == ""


def test_cmd_update_task_empty_string_clears_ticket(temp_db, capsys):
    """Same as notes: empty ticket_id/url should actually clear."""
    cli_mod._ensure_core()
    temp_db.update_task("proj-a", "task-1",
                        ticket_id="ALT-1", ticket_url="https://x/ALT-1")
    row = temp_db.get_task("proj-a", "task-1")
    assert row["ticket_id"] == "ALT-1"

    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id="", ticket_url="",
        type=None, group=None, follow_ups=None,
    )
    cli_mod.cmd_update_task(args)
    row = temp_db.get_task("proj-a", "task-1")
    assert row["ticket_id"] == ""
    assert row["ticket_url"] == ""


def test_cmd_update_task_follow_ups_overwrite_prior(temp_db, capsys):
    """Each invocation REPLACES the stored follow-up list (argparse action=append
    passes the full set from that call). Documenting the current behaviour so
    callers know: to add a follow-up, include prior ones in the call too."""
    cli_mod._ensure_core()
    temp_db.update_task("proj-a", "task-1", follow_ups=["old"])

    args = _make_args(
        project="proj-a", task_id="task-1",
        status=None, description=None, notes=None,
        priority=None, ticket_id=None, ticket_url=None,
        type=None, group=None,
        follow_ups=["new-only"],
    )
    cli_mod.cmd_update_task(args)
    task = temp_db.get_task("proj-a", "task-1")
    assert task["follow_ups"] == ["new-only"]


# ============================================================
# rename-task
# ============================================================


def test_cmd_rename_task(temp_db, capsys):
    """rename-task moves task from old ID to new ID."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", old_id="task-3", new_id="task-3-renamed")
    cli_mod.cmd_rename_task(args)
    out = capsys.readouterr().out
    assert "Renamed" in out

    # Old ID should be gone
    get_old = _make_args(project="proj-a", task_id="task-3")
    with pytest.raises(SystemExit):
        cli_mod.cmd_get_task(get_old)

    # New ID should exist
    capsys.readouterr()  # clear
    get_new = _make_args(project="proj-a", task_id="task-3-renamed", json=True)
    cli_mod.cmd_get_task(get_new)
    data = json.loads(capsys.readouterr().out)
    assert data["id"] == "task-3-renamed"
    assert data["description"] == "Third task"


def test_cmd_rename_task_json(temp_db, capsys):
    """rename-task --json outputs the renamed task."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", old_id="task-3", new_id="task-3b", json=True)
    cli_mod.cmd_rename_task(args)
    data = json.loads(capsys.readouterr().out)
    assert data["task_id"] == "task-3b"


def test_cmd_rename_task_source_not_found(temp_db):
    """rename-task on nonexistent old ID exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", old_id="no-such", new_id="whatever")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_rename_task(args)
    assert exc_info.value.code == 1


def test_cmd_rename_task_target_exists(temp_db):
    """rename-task where new_id already exists exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", old_id="task-1", new_id="task-2")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_rename_task(args)
    assert exc_info.value.code == 1


# ============================================================
# close-task
# ============================================================


def test_cmd_close_task(temp_db, capsys):
    """close-task sets status to closed."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1", reason="No longer needed")
    cli_mod.cmd_close_task(args)
    out = capsys.readouterr().out
    assert "Closed" in out

    get_args = _make_args(project="proj-a", task_id="task-1", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "closed"
    assert "No longer needed" in data.get("notes", "")


def test_cmd_close_task_no_reason(temp_db, capsys):
    """close-task without reason still closes."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1", reason="")
    cli_mod.cmd_close_task(args)
    out = capsys.readouterr().out
    assert "Closed" in out


def test_cmd_close_task_json(temp_db, capsys):
    """close-task --json outputs task as JSON."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1", reason="done", json=True)
    cli_mod.cmd_close_task(args)
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "closed"


def test_cmd_close_task_not_found(temp_db):
    """close-task on nonexistent task exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="ghost", reason="")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_close_task(args)
    assert exc_info.value.code == 1


# ============================================================
# delete-task (disabled)
# ============================================================


def test_cmd_delete_task_disabled(temp_db):
    """delete-task should always exit with error (disabled)."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-1", force=False)
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_delete_task(args)
    assert exc_info.value.code == 1


# ============================================================
# update-project
# ============================================================


def test_cmd_update_project_design_doc(temp_db, capsys):
    """update-project --design-doc sets the design doc URL."""
    cli_mod._ensure_core()
    import app_state
    args = _make_args(
        project="proj-a",
        name=None, description=None, repo=None, jira=None,
        has_tickets=None, design_doc="https://docs.example.com/design",
    )
    cli_mod.cmd_update_project(args)
    out = capsys.readouterr().out
    assert "Updated" in out

    proj = app_state._db.get_project("proj-a")
    assert proj["design_doc"] == "https://docs.example.com/design"


def test_cmd_update_project_name(temp_db, capsys):
    """update-project --name changes the project name."""
    cli_mod._ensure_core()
    import app_state
    args = _make_args(
        project="proj-a",
        name="New Name", description=None, repo=None, jira=None,
        has_tickets=None, design_doc=None,
    )
    cli_mod.cmd_update_project(args)
    out = capsys.readouterr().out
    assert "Updated" in out

    proj = app_state._db.get_project("proj-a")
    assert proj["name"] == "New Name"


def test_cmd_update_project_json(temp_db, capsys):
    """update-project --json outputs the updated project."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a",
        name="JSON Test", description=None, repo=None, jira=None,
        has_tickets=None, design_doc=None,
        json=True,
    )
    cli_mod.cmd_update_project(args)
    data = json.loads(capsys.readouterr().out)
    assert data["name"] == "JSON Test"


def test_cmd_update_project_no_fields(temp_db):
    """update-project with no fields exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="proj-a",
        name=None, description=None, repo=None, jira=None,
        has_tickets=None, design_doc=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_update_project(args)
    assert exc_info.value.code == 1


def test_cmd_update_project_not_found(temp_db):
    """update-project on nonexistent project exits with error."""
    cli_mod._ensure_core()
    args = _make_args(
        project="no-proj",
        name="X", description=None, repo=None, jira=None,
        has_tickets=None, design_doc=None,
    )
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_update_project(args)
    assert exc_info.value.code == 1


# ============================================================
# add-dep / remove-dep
# ============================================================


def test_cmd_add_dep(temp_db, capsys):
    """add-dep creates a dependency between two tasks."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="task-3", depends_on="task-1")
    cli_mod.cmd_add_dep(args)
    out = capsys.readouterr().out
    assert "Added dependency" in out

    # Verify
    get_args = _make_args(project="proj-a", task_id="task-3", json=True)
    cli_mod.cmd_get_task(get_args)
    data = json.loads(capsys.readouterr().out)
    assert "task-1" in data.get("dependencies", [])


def test_cmd_remove_dep(temp_db, capsys):
    """remove-dep removes a dependency."""
    cli_mod._ensure_core()
    # task-2 depends on task-1 (from fixture)
    args = _make_args(project="proj-a", task_id="task-2", depends_on="task-1")
    cli_mod.cmd_remove_dep(args)
    out = capsys.readouterr().out
    assert "Removed dependency" in out or "no longer depends" in out


def test_cmd_add_dep_nonexistent_task(temp_db, capsys):
    """add-dep with nonexistent task exits with error AND points the user
    at `list-tasks` so they can find the right id."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", task_id="ghost", depends_on="task-1")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_add_dep(args)
    assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ghost" in err or "not found" in err.lower()
    # The helpful hint steers the user to the list-tasks fix.
    assert "list-tasks proj-a" in err


def test_cmd_remove_dep_swallows_runtime_errors_with_hint(temp_db, capsys):
    """`remove_dependency` raising ValueError (e.g. dep not found in DB)
    must surface through the same hint-bearing error pattern as add-dep.
    Ensures both directions of the graph edit stay user-friendly."""
    cli_mod._ensure_core()

    # Patch the core function to raise a ValueError so we can assert the
    # CLI formats it with the `list-tasks` hint.
    from unittest.mock import patch
    args = _make_args(project="proj-a", task_id="task-1", depends_on="ghost")
    with patch(
        "common.tasks.remove_dependency",
        side_effect=ValueError("Dependency target 'ghost' not found"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            cli_mod.cmd_remove_dep(args)
        assert exc_info.value.code == 1
    err = capsys.readouterr().err
    assert "ghost" in err
    assert "list-tasks proj-a" in err


# ============================================================
# --json flag on various commands
# ============================================================


def test_cmd_fetch_tasks_json(temp_db, capsys):
    """fetch-tasks --json outputs full task detail dict."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", json=True)
    cli_mod.cmd_fetch_tasks(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, dict)
    assert "task-1" in data


def test_cmd_fetch_tasks_text(temp_db, capsys):
    """fetch-tasks text output includes task details."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", json=False)
    cli_mod.cmd_fetch_tasks(args)
    out = capsys.readouterr().out
    assert "task-1" in out
    assert "First task" in out
    # task-2 has deps
    assert "dependencies" in out or "task-1" in out


def test_cmd_fetch_tasks_invalid_project(temp_db):
    """fetch-tasks on nonexistent project exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="nope", json=False)
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_fetch_tasks(args)
    assert exc_info.value.code == 1


def test_cmd_fetch_tasks_renders_ticket_notes_deps(temp_db, capsys):
    """fetch-tasks text output must show ticket link, notes, and deps
    when the task has them (covers branches untested in the basic test)."""
    temp_db.update_task(
        "proj-a", "task-1",
        ticket_id="ALT-42", ticket_url="https://x/ALT-42",
        notes="do the thing", priority=1, type="bug",
    )
    cli_mod.cmd_fetch_tasks(_make_args(project="proj-a", json=False))
    out = capsys.readouterr().out
    assert "ALT-42" in out
    assert "do the thing" in out
    # task-2 depends on task-1 per the temp_db seed.
    assert "dependencies" in out
    # priority != 5 so it should surface.
    assert "priority: 1" in out
    assert "type: bug" in out


def test_cmd_fetch_tasks_empty_project_prints_nothing(temp_db, capsys):
    """fetch-tasks on a project with zero tasks must not raise and prints
    no task sections (just an empty loop)."""
    cli_mod.cmd_fetch_tasks(_make_args(project="proj-b", json=False))
    out = capsys.readouterr().out
    # Empty project output is just whitespace -- no --- task --- headers.
    assert "---" not in out


def test_cmd_fetch_tasks_renders_pr_line(temp_db, capsys):
    """If a task has PRs, fetch-tasks must print a PR summary line per PR."""
    temp_db.add_pr(
        "proj-a", "task-1",
        number=42, url="https://github.com/example/repo/pull/42",
        status="open", title="Fix bug",
    )
    cli_mod.cmd_fetch_tasks(_make_args(project="proj-a", json=False))
    out = capsys.readouterr().out
    assert "#42" in out
    assert "Fix bug" in out


# ============================================================
# stats command
# ============================================================


def test_cmd_stats_text(temp_db, capsys):
    """stats command outputs project progress."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a")
    cli_mod.cmd_stats(args)
    out = capsys.readouterr().out
    assert "Project A" in out


def test_cmd_stats_json(temp_db, capsys):
    """stats --json outputs structured data."""
    cli_mod._ensure_core()
    args = _make_args(project="proj-a", json=True)
    cli_mod.cmd_stats(args)
    data = json.loads(capsys.readouterr().out)
    assert data["project"] == "proj-a"
    assert "progress" in data
    assert "task_counts" in data


def test_cmd_stats_invalid_project(temp_db):
    """stats on nonexistent project exits with error."""
    cli_mod._ensure_core()
    args = _make_args(project="nope")
    with pytest.raises(SystemExit) as exc_info:
        cli_mod.cmd_stats(args)
    assert exc_info.value.code == 1


# ============================================================
# build_parser and _DISPATCH
# ============================================================


def test_build_parser_has_all_commands():
    """Parser should have all expected subcommands."""
    parser = cli_mod.build_parser()
    # Check a known set of command names exists in _DISPATCH
    expected = [
        "list-projects", "list-tasks", "get-task", "create-task",
        "update-task", "add-dep", "remove-dep", "close-task",
        "delete-task", "rename-task", "update-project",
        "list-prs", "add-pr", "remove-pr", "stats",
    ]
    for cmd in expected:
        assert cmd in cli_mod._DISPATCH, f"{cmd} missing from _DISPATCH"


# ============================================================
# Color / formatting helpers
# ============================================================


def test_status_color_mapping():
    """_status_color returns the input for unknown statuses."""
    assert "done" in cli_mod._status_color("done")
    assert "in_progress" in cli_mod._status_color("in_progress")
    assert "blocked" in cli_mod._status_color("blocked")
    # Unknown status returned as-is
    assert cli_mod._status_color("custom") == "custom"
    assert cli_mod._status_color("") == ""
    assert cli_mod._status_color(None) == ""


def test_table_formatter():
    """_table produces a properly formatted table."""
    result = cli_mod._table(
        ["A", "B"],
        [("hello", "world"), ("foo", "bar")],
    )
    assert "A" in result
    assert "hello" in result
    assert "foo" in result


# ============================================================
# events command
# ============================================================


def test_cmd_events_text(temp_db, capsys):
    """events command runs without error on empty events table."""
    cli_mod._ensure_core()
    args = _make_args(limit=5)
    cli_mod.cmd_events(args)
    out = capsys.readouterr().out
    assert "Events" in out


def test_cmd_events_json(temp_db, capsys):
    """events --json outputs structured event data."""
    cli_mod._ensure_core()
    args = _make_args(limit=5, json=True)
    cli_mod.cmd_events(args)
    data = json.loads(capsys.readouterr().out)
    assert "events" in data
    assert "unread" in data
    assert "total" in data


def _emit_event_row(temp_db, **overrides):
    """Insert one row into the events table for testing cmd_events rendering."""
    import app_state
    fields = {
        "id": overrides.get("id", "e1"),
        "source": overrides.get("source", "github"),
        "source_id": overrides.get("source_id", ""),
        "title": overrides.get("title", "Some event"),
        "message": overrides.get("message", "details"),
        "type": overrides.get("type", "info"),
        "severity": overrides.get("severity", "info"),
        "url": overrides.get("url", ""),
        "ts": overrides.get("ts", "2026-04-17T10:00:00"),
        "read": overrides.get("read", 0),
        "session": overrides.get("session", ""),
    }
    with sqlite3.connect(str(app_state._NOTIF_DB_PATH)) as conn:
        conn.execute(
            "INSERT INTO events (id, source, source_id, title, message, type, severity, url, ts, read, session) "
            "VALUES (:id, :source, :source_id, :title, :message, :type, :severity, :url, :ts, :read, :session)",
            fields,
        )


def test_cmd_events_renders_rows_with_title_and_message(temp_db, capsys):
    """Non-JSON output includes every row's title and first 80 chars of message."""
    _emit_event_row(temp_db, id="e1", title="PR #42 review", message="alice requested review")
    cli_mod.cmd_events(_make_args(limit=10))
    out = capsys.readouterr().out
    assert "PR #42 review" in out
    assert "alice requested review" in out


def test_cmd_events_shows_unread_and_total_counts(temp_db, capsys):
    """Header reports X unread / Y total."""
    _emit_event_row(temp_db, id="e1", read=0)
    _emit_event_row(temp_db, id="e2", read=1)
    _emit_event_row(temp_db, id="e3", read=0)
    cli_mod.cmd_events(_make_args(limit=10))
    out = capsys.readouterr().out
    assert "2 unread" in out
    assert "3 total" in out


def test_cmd_events_respects_limit(temp_db, capsys):
    """--limit caps the number of rows shown in output."""
    for i in range(5):
        _emit_event_row(temp_db, id=f"ev-{i}", title=f"event {i}")
    cli_mod.cmd_events(_make_args(limit=2))
    out = capsys.readouterr().out
    # Only 2 events should appear (newest-first ordering); total is still 5
    # but only 2 titles are printed.
    shown = sum(1 for i in range(5) if f"event {i}" in out)
    assert shown == 2


def test_cmd_events_message_truncated_at_80_chars(temp_db, capsys):
    """Long messages are truncated to 80 chars in the detail line."""
    long_msg = "x" * 200
    _emit_event_row(temp_db, id="e1", message=long_msg)
    cli_mod.cmd_events(_make_args(limit=5))
    out = capsys.readouterr().out
    # Should contain exactly 80 x's in a row, but NOT 81.
    assert "x" * 80 in out
    assert "x" * 81 not in out


# ============================================================
# list-prs / add-pr / remove-pr (PR CRUD via CLI)
# ============================================================


def test_cmd_list_prs_empty(temp_db, capsys):
    """list-prs with no PRs prints a dim notice."""
    args = _make_args(status="", search="")
    cli_mod.cmd_list_prs(args)
    out = capsys.readouterr().out
    assert "No PRs" in out


def test_cmd_list_prs_json_empty(temp_db, capsys):
    """list-prs --json returns a groups object even when empty."""
    args = _make_args(status="", search="", json=True)
    cli_mod.cmd_list_prs(args)
    data = json.loads(capsys.readouterr().out)
    assert "groups" in data


def test_cmd_add_pr_and_list(temp_db, capsys):
    """add-pr adds a PR, then list-prs shows it."""
    cli_mod.cmd_add_pr(_make_args(
        project="proj-a", task_id="task-1",
        number=42, url="https://github.com/example/repo/pull/42",
        status="open", title="Add feature",
    ))
    out = capsys.readouterr().out
    assert "Added PR #42" in out

    cli_mod.cmd_list_prs(_make_args(status="", search="", json=True))
    data = json.loads(capsys.readouterr().out)
    groups = data.get("groups", {})
    numbers = [pr["number"] for group in groups.values() for pr in group.get("prs", [])]
    assert 42 in numbers


def test_cmd_add_pr_invalid_task(temp_db, capsys):
    """add-pr to a non-existent task exits non-zero with a clear error."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_add_pr(_make_args(
            project="proj-a", task_id="does-not-exist",
            number=99, url="https://github.com/x/y/pull/99",
            status="open", title="required",
        ))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "does-not-exist" in err


def test_cmd_remove_pr(temp_db, capsys):
    """remove-pr deletes an existing PR."""
    cli_mod.cmd_add_pr(_make_args(
        project="proj-a", task_id="task-1",
        number=7, url="https://github.com/a/b/pull/7", status="open", title="x",
    ))
    capsys.readouterr()  # discard
    cli_mod.cmd_remove_pr(_make_args(project="proj-a", task_id="task-1", number=7))
    out = capsys.readouterr().out
    assert "Removed PR #7" in out


def test_cmd_remove_pr_not_found(temp_db, capsys):
    """remove-pr for a missing PR exits 1."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_remove_pr(_make_args(project="proj-a", task_id="task-1", number=12345))
    assert exc.value.code == 1


def test_cmd_remove_pr_invalid_task(temp_db, capsys):
    """remove-pr on a missing task exits 1."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_remove_pr(_make_args(project="proj-a", task_id="nope", number=1))
    assert exc.value.code == 1


def test_cmd_add_pr_empty_title_emits_clean_error(temp_db, capsys):
    """Regression: `add-pr` without --title used to raise ValueError and
    crash with a Python traceback (common.prs.add_pr requires a title).
    The CLI must surface a helpful hint instead."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_add_pr(_make_args(
            project="proj-a", task_id="task-1",
            number=777, url="https://github.com/example/repo/pull/777",
            status="open", title="",  # explicit empty
        ))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    # Exact error text from common.prs.add_pr
    assert "PR title is required" in err
    # CLI-added hint that tells the user how to recover
    assert "--title" in err and "pr-detail" in err
    # No stack-trace leakage
    assert "Traceback" not in err and "ValueError" not in err


def test_cmd_add_pr_whitespace_title_emits_clean_error(temp_db, capsys):
    """A title made entirely of whitespace should be treated as empty."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_add_pr(_make_args(
            project="proj-a", task_id="task-1",
            number=778, url="https://github.com/example/repo/pull/778",
            status="open", title="   ",
        ))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "PR title is required" in err


# ============================================================
# list-sessions
# ============================================================


def test_cmd_list_sessions_empty(temp_db, capsys):
    """list-sessions with no sessions prints a notice."""
    cli_mod.cmd_list_sessions(_make_args())
    out = capsys.readouterr().out
    assert "No sessions" in out


def test_cmd_list_sessions_json_empty(temp_db, capsys):
    """list-sessions --json returns a (possibly empty) dict."""
    cli_mod.cmd_list_sessions(_make_args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert isinstance(data, dict)


def test_cmd_list_sessions_with_data(temp_db, capsys):
    """list-sessions renders a row per session when present."""
    temp_db.create_session("task-1", "proj-a", "task-1")
    cli_mod.cmd_list_sessions(_make_args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert "proj-a" in data
    assert any(s.get("task_id") == "task-1" for s in data["proj-a"]["sessions"])


# ============================================================
# delete-task is disabled: must always refuse and exit 1
# ============================================================


def test_cmd_delete_task_always_disabled(temp_db, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_delete_task(_make_args(project="proj-a", task_id="task-1"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "disabled" in err
    # Task still exists afterwards.
    assert temp_db.get_task("proj-a", "task-1") is not None


# ============================================================
# check-status
# ============================================================


def test_cmd_check_status_unchanged(temp_db, capsys):
    """check-status on a task with no ticket leaves status unchanged."""
    cli_mod.cmd_check_status(_make_args(project="proj-a", task_id="task-1"))
    out = capsys.readouterr().out
    # Should print either unchanged or changed; exit 0 either way.
    assert "Status" in out or "status" in out


def test_cmd_check_status_not_found(temp_db, capsys):
    """check-status on a missing task exits 1."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_check_status(_make_args(project="proj-a", task_id="ghost"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ghost" in err


# ============================================================
# _error helper: shape / hint / exit-code / "Error:" prefix dedup
# ============================================================


def test_error_helper_prefixes_error_on_stderr(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_mod._error("something broke")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    # Must be on stderr, include the literal "Error:" once, and end without
    # trailing whitespace noise beyond the ANSI reset.
    assert "Error: something broke" in err
    # Sanity: never double-prefix
    assert err.count("Error:") == 1


def test_error_helper_preserves_existing_prefix(capsys):
    with pytest.raises(SystemExit):
        cli_mod._error("Error: already prefixed")
    err = capsys.readouterr().err
    # Helper must not add "Error: " a second time when the caller already did.
    assert err.count("Error:") == 1


def test_error_helper_prints_hint_on_second_line(capsys):
    with pytest.raises(SystemExit):
        cli_mod._error("oops", hint="try X")
    err = capsys.readouterr().err
    assert "Error: oops" in err
    assert "try X" in err
    # Hint must be on its own line after the error
    lines = [line for line in err.splitlines() if line.strip()]
    assert len(lines) >= 2


def test_error_helper_uses_code_param(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_mod._error("fatal", code=2)
    assert exc.value.code == 2


def test_emit_jsonl_event_writes_line_when_json(capsys):
    """_emit_jsonl_event prints one compact JSON line and returns True."""
    out = cli_mod._emit_jsonl_event(_make_args(json=True), {"phase": "start"})
    assert out is True
    captured = capsys.readouterr().out.strip()
    assert json.loads(captured) == {"phase": "start"}
    # Must be single-line (no indent), distinct from _emit_json_if_requested.
    assert "\n" not in captured.rstrip("\n")


def test_emit_jsonl_event_returns_false_when_not_json(capsys):
    out = cli_mod._emit_jsonl_event(_make_args(json=False), {"phase": "start"})
    assert out is False
    assert capsys.readouterr().out == ""


def test_emit_json_vs_jsonl_format_differ(capsys):
    """Sanity check: pretty JSON is multiline, JSONL is single-line."""
    cli_mod._emit_json_if_requested(_make_args(json=True), {"a": 1, "b": 2})
    pretty = capsys.readouterr().out
    cli_mod._emit_jsonl_event(_make_args(json=True), {"a": 1, "b": 2})
    compact = capsys.readouterr().out
    assert "\n" in pretty.rstrip("\n")  # pretty-print has internal newlines
    assert "\n" not in compact.rstrip("\n")  # compact is one line


# ============================================================
# open-session error path (historically called undefined _error)
# ============================================================


def test_cmd_open_session_invalid_action_raises_cleanly(temp_db, capsys):
    """open-session with an invalid action should exit 1 with a stderr error,
    not a NameError."""
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_open_session(_make_args(
            task_id="task-1", project="proj-a",
            action="does-not-exist", prompt=None,
            pr_number=None, pr_repo=None,
        ))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Error:" in err


# ============================================================
# Commands that wrap system (usage / pr-stats / auth-status / renew-cert)
# Must not hit any real external systems (cloud, gh, tmux, etc).
# ============================================================


def test_cmd_usage_with_data(temp_db, capsys):
    """usage prints daily/weekly/monthly fields when available."""
    from common import system
    fake = {"daily": 1.5, "weekly": 10.2, "monthly": 42.0, "tier": "pro"}
    with patch.object(system, "get_usage", return_value=fake):
        cli_mod.cmd_usage(_make_args())
    out = capsys.readouterr().out
    assert "AI Usage" in out
    assert "1.5" in out
    assert "pro" in out


def test_cmd_usage_json(temp_db, capsys):
    from common import system
    fake = {"daily": 2.0}
    with patch.object(system, "get_usage", return_value=fake):
        cli_mod.cmd_usage(_make_args(json=True))
    assert json.loads(capsys.readouterr().out) == fake


def test_cmd_usage_empty(temp_db, capsys):
    """Empty usage data falls back to a dim 'No data' message."""
    from common import system
    with patch.object(system, "get_usage", return_value={}):
        cli_mod.cmd_usage(_make_args())
    out = capsys.readouterr().out
    assert "No usage data" in out


def test_cmd_pr_stats_with_data(temp_db, capsys):
    """pr-stats renders per-quarter rows plus all-time summary."""
    from common import system
    fake = {
        "quarters": [
            {"period": "2026Q1", "universe": 1, "runtime": 2, "repo": 3, "total": 6},
            {"period": "2026Q2", "universe": 4, "runtime": 5, "repo": 6, "total": 15},
        ],
        "all_time": {"universe": 5, "runtime": 7, "repo": 9, "total": 21},
        "weekly": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    }
    with patch.object(system, "get_workstats", return_value=fake):
        cli_mod.cmd_pr_stats(_make_args())
    out = capsys.readouterr().out
    assert "PR Stats by Quarter" in out
    assert "2026Q1" in out
    assert "2026Q2" in out
    assert "All-time" in out
    # weekly list last 8 entries shown (so '2' ... '9' present, '0' not)
    assert "9" in out


def test_cmd_pr_stats_empty(temp_db, capsys):
    from common import system
    with patch.object(system, "get_workstats", return_value={}):
        cli_mod.cmd_pr_stats(_make_args())
    out = capsys.readouterr().out
    assert "No PR stats" in out


def test_cmd_pr_stats_json(temp_db, capsys):
    from common import system
    fake = {"quarters": []}
    with patch.object(system, "get_workstats", return_value=fake):
        cli_mod.cmd_pr_stats(_make_args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data == fake


def test_cmd_auth_status_with_certs(temp_db, capsys):
    """auth-status prints one row per cert with status + remaining time.
    Uses neutral fixture cert ids so the test stays vendor-agnostic;
    real provider integration is covered in each provider's own
    test tree."""
    from common import system
    fake = {
        "cert_a": {"name": "cert_a", "status": "ok", "remaining_seconds": 3661, "note": ""},
        "cert_b": {"name": "Cert B", "status": "expired",
                   "remaining_seconds": -1, "note": "re-auth"},
    }
    with patch.object(system, "get_certs", return_value=fake):
        cli_mod.cmd_auth_status(_make_args())
    out = capsys.readouterr().out
    assert "cert_a" in out
    # 3661s ~= 1h 1m
    assert "1h 1m" in out
    assert "re-auth" in out


def test_cmd_auth_status_json(temp_db, capsys):
    from common import system
    fake = {"cert_a": {"name": "cert_a", "status": "ok"}}
    with patch.object(system, "get_certs", return_value=fake):
        cli_mod.cmd_auth_status(_make_args(json=True))
    data = json.loads(capsys.readouterr().out)
    assert data == fake


def test_cmd_renew_cert_unknown_exits_with_hint(temp_db, capsys):
    """renew-cert with an unknown cert id exits 1 and shows the valid options."""
    from common import system
    # Seed a single registered cert so the hint has a known option to list.
    with patch.object(system, "get_certs",
                      return_value={"cert_a": {"name": "cert_a"}}), \
         patch.object(system, "renew_cert", return_value=None):
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_renew_cert(_make_args(cert_id="bogus"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "bogus" in err
    assert "cert_a" in err  # hint lists valid options


def test_cmd_renew_cert_success(temp_db, capsys):
    from common import system
    with patch.object(system, "renew_cert", return_value={"ok": True, "output": ""}):
        cli_mod.cmd_renew_cert(_make_args(cert_id="cert_a"))
    out = capsys.readouterr().out
    assert "Renewed" in out


def test_cmd_renew_cert_failure(temp_db, capsys):
    from common import system
    with patch.object(system, "renew_cert", return_value={"ok": False, "output": "server down"}):
        cli_mod.cmd_renew_cert(_make_args(cert_id="cert_a"))
    out = capsys.readouterr().out
    assert "Renewal failed" in out
    assert "server down" in out


def test_cmd_renew_cert_json_success(temp_db, capsys):
    from common import system
    payload = {"ok": True, "output": "ok"}
    with patch.object(system, "renew_cert", return_value=payload):
        cli_mod.cmd_renew_cert(_make_args(cert_id="cert_a", json=True))
    data = json.loads(capsys.readouterr().out)
    assert data == payload


# ============================================================
# pr-detail and sync-prs (must mock the underlying prs layer;
# never allow a real gh CLI call).
# ============================================================


def test_cmd_pr_detail_found(temp_db, capsys):
    from common import prs as prs_core
    fake = {
        "number": 42, "title": "Fix bug", "state": "OPEN",
        "author": {"login": "alice"},
        "headRefName": "fix", "baseRefName": "main",
        "additions": 10, "deletions": 5, "files": [{"path": "a"}],
        "reviewDecision": "APPROVED",
        "statusCheckRollup": [
            {"name": "build", "conclusion": "success", "status": "completed"},
        ],
    }
    with patch.object(prs_core, "get_pr_detail", return_value=fake):
        cli_mod.cmd_pr_detail(_make_args(repo="example/repo", number=42))
    out = capsys.readouterr().out
    assert "#42" in out
    assert "Fix bug" in out
    assert "alice" in out
    assert "build" in out


def test_cmd_pr_detail_not_found(temp_db, capsys):
    from common import prs as prs_core
    with patch.object(prs_core, "get_pr_detail", return_value=None):
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_pr_detail(_make_args(repo="example/repo", number=999))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "999" in err


def test_cmd_pr_detail_json(temp_db, capsys):
    from common import prs as prs_core
    fake = {"number": 1, "title": "t", "state": "OPEN"}
    with patch.object(prs_core, "get_pr_detail", return_value=fake):
        cli_mod.cmd_pr_detail(_make_args(repo="a/b", number=1, json=True))
    assert json.loads(capsys.readouterr().out) == fake


def test_cmd_sync_prs_streams_progress_text(temp_db, capsys):
    """sync-prs renders human-readable phases from the generator."""
    from common import prs as prs_core
    events = iter([
        {"phase": "start", "full": False},
        {"phase": "dirty", "count": 3},
        {"phase": "dirty_update", "current": 3, "total": 3},
        {"phase": "discover", "discovered": 2},
        {"phase": "update", "current": 2, "total": 2, "updated": 2},
        {"phase": "done", "discovered": 2, "updated": 2, "total": 5},
    ])
    with patch.object(prs_core, "sync_prs_generator", return_value=events):
        cli_mod.cmd_sync_prs(_make_args(full=False))
    out = capsys.readouterr().out
    assert "Syncing PRs" in out
    assert "dirty" in out.lower() or "Updating" in out
    assert "Done" in out


def test_cmd_sync_prs_json_emits_events_as_jsonl(temp_db, capsys):
    """sync-prs --json emits one JSON object per progress event, then stops at done."""
    from common import prs as prs_core
    events = iter([
        {"phase": "start"},
        {"phase": "update", "current": 1, "total": 2},
        {"phase": "done", "total": 2},
        {"phase": "after_done"},  # should NOT be printed
    ])
    with patch.object(prs_core, "sync_prs_generator", return_value=events):
        cli_mod.cmd_sync_prs(_make_args(full=False, json=True))
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    # Must include start / update / done, but not after_done.
    phases = [json.loads(line).get("phase") for line in lines]
    assert "start" in phases
    assert "update" in phases
    assert "done" in phases
    assert "after_done" not in phases


def test_cmd_sync_prs_full_flag_forwarded(temp_db, capsys):
    """--full is forwarded to sync_prs_generator(full=True)."""
    from common import prs as prs_core
    with patch.object(prs_core, "sync_prs_generator", return_value=iter([{"phase": "done"}])) as mock_gen:
        cli_mod.cmd_sync_prs(_make_args(full=True))
    mock_gen.assert_called_once_with(full=True)


# ============================================================
# open-session / kill-session / session-status (mock sessions)
# ============================================================


def test_cmd_open_session_success_new(temp_db, capsys):
    from common import sessions as sess
    fake = {"session": "task-1", "new": True, "prompt": "hello"}
    with patch.object(sess, "open_session", return_value=fake):
        cli_mod.cmd_open_session(_make_args(
            task_id="task-1", project="proj-a", action="open", prompt=None,
            pr_number=None, pr_repo=None,
        ))
    out = capsys.readouterr().out
    assert "Opened new session" in out
    assert "task-1" in out
    assert "Prompt ready" in out  # prompt size echoed


def test_cmd_open_session_resumed(temp_db, capsys):
    from common import sessions as sess
    fake = {"session": "task-1", "new": False, "prompt": ""}
    with patch.object(sess, "open_session", return_value=fake):
        cli_mod.cmd_open_session(_make_args(
            task_id="task-1", project="proj-a", action=None, prompt=None,
            pr_number=None, pr_repo=None,
        ))
    out = capsys.readouterr().out
    assert "Resumed session" in out


def test_cmd_open_session_json(temp_db, capsys):
    from common import sessions as sess
    fake = {"session": "task-1", "new": True, "prompt": ""}
    with patch.object(sess, "open_session", return_value=fake):
        cli_mod.cmd_open_session(_make_args(
            task_id="task-1", project="proj-a", action=None, prompt=None,
            pr_number=None, pr_repo=None, json=True,
        ))
    assert json.loads(capsys.readouterr().out) == fake


def test_cmd_open_session_pr_context_passes_pr_args(temp_db, capsys):
    """`--pr-number` + `--pr-repo` should reach core.open_session so
    pr-context actions (fix-ci / auto-pr-tend / ...) can be triggered
    from CLI with the PR they target."""
    from common import sessions as sess
    fake = {"session": "task-1", "new": True, "prompt": ""}
    with patch.object(sess, "open_session", return_value=fake) as mock_open:
        cli_mod.cmd_open_session(_make_args(
            task_id="task-1", project="proj-a",
            action="fix-ci", prompt=None,
            pr_number=12345, pr_repo="example/repo",
        ))
    _, kwargs = mock_open.call_args
    assert kwargs["pr_number"] == 12345
    assert kwargs["pr_repo"] == "example/repo"
    assert kwargs["action_id"] == "fix-ci"


def test_cmd_open_review_session_success(temp_db, capsys):
    """open-review-session is the CLI mirror of clicking a Review Card
    button in the web UI."""
    from common import reviews as rev
    fake = {"session": "review-example-repo-42", "new": True, "prompt": "do review"}
    with patch.object(rev, "open_review_session", return_value=fake):
        cli_mod.cmd_open_review_session(_make_args(
            url="https://github.com/example/repo/pull/42",
            action="review-pr", prompt=None,
        ))
    out = capsys.readouterr().out
    assert "Opened new review session" in out
    assert "review-example-repo-42" in out


def test_cmd_send_message_session_missing(temp_db, capsys):
    """send-message must refuse to paste into a non-existent session
    instead of silently no-op'ing (the user would never know why their
    message didn't show up)."""
    from adapters import tmux as adapter
    with patch.object(adapter, "session_exists", return_value=False):
        with pytest.raises(SystemExit):
            cli_mod.cmd_send_message(_make_args(
                session_name="nope", message="hi",
                no_wait=False, timeout=30,
            ))


def test_cmd_send_message_no_wait_pastes_immediately(temp_db, capsys):
    """--no-wait skips the readiness probe -- useful when the caller
    knows the agent is already idle and doesn't want to add 1-2s of poll
    latency on top."""
    from adapters import tmux as adapter
    paste_calls = []
    with patch.object(adapter, "session_exists", return_value=True), \
         patch.object(adapter, "paste_text",
                      side_effect=lambda n, t: paste_calls.append((n, t))), \
         patch.object(adapter, "wait_until_ready",
                      side_effect=AssertionError("must not be called when --no-wait")):
        cli_mod.cmd_send_message(_make_args(
            session_name="task-1", message="hello",
            no_wait=True, timeout=30,
        ))
    assert paste_calls == [("task-1", "hello")]


def test_cmd_kill_session(temp_db, capsys):
    from common import sessions as sess
    with patch.object(sess, "kill_session", return_value={"killed": True}):
        cli_mod.cmd_kill_session(_make_args(session_name="task-1"))
    out = capsys.readouterr().out
    assert "Killed session" in out
    assert "task-1" in out


def test_cmd_kill_session_json(temp_db, capsys):
    from common import sessions as sess
    with patch.object(sess, "kill_session", return_value={"killed": True}):
        cli_mod.cmd_kill_session(_make_args(session_name="task-1", json=True))
    assert json.loads(capsys.readouterr().out) == {"killed": True}


def test_cmd_resume_session_resumed_with_uuid(temp_db, capsys):
    """resumed action includes the agent session UUID in the success line
    so the user can copy it straight into `agent resume <uuid>` if they
    want to confirm manually."""
    from common import sessions as sess
    fake = {"session": "task-1", "action": "resumed", "running": True,
            "agent_session_id": "abc-123"}
    with patch.object(sess, "resume_session", return_value=fake):
        cli_mod.cmd_resume_session(_make_args(session_name="task-1"))
    out = capsys.readouterr().out
    assert "Resumed session" in out
    assert "task-1" in out
    assert "abc-123" in out


def test_cmd_resume_session_relaunched_warns_history_lost(temp_db, capsys):
    """Legacy session row without an agent_session_id -> fresh launch.
    The CLI must make clear that conversation history was NOT restored."""
    from common import sessions as sess
    fake = {"session": "legacy", "action": "relaunched", "running": True,
            "agent_session_id": ""}
    with patch.object(sess, "resume_session", return_value=fake):
        cli_mod.cmd_resume_session(_make_args(session_name="legacy"))
    out = capsys.readouterr().out
    assert "Relaunched" in out
    assert "legacy" in out
    assert "without conversation history" in out


def test_cmd_resume_session_noop_when_already_running(temp_db, capsys):
    from common import sessions as sess
    fake = {"session": "alive", "action": "noop", "running": True}
    with patch.object(sess, "resume_session", return_value=fake):
        cli_mod.cmd_resume_session(_make_args(session_name="alive"))
    out = capsys.readouterr().out
    assert "already running" in out


def test_cmd_resume_session_unknown_session_errors_with_hint(temp_db, capsys):
    """ValueError from core surfaces as a clean error + hint pointing
    at list-sessions (so the user can see what IS known)."""
    from common import sessions as sess
    def raise_unknown(*_a, **_kw):
        raise ValueError("Session not found: ghost")
    with patch.object(sess, "resume_session", side_effect=raise_unknown):
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_resume_session(_make_args(session_name="ghost"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "ghost" in err
    assert "list-sessions" in err


def test_cmd_session_status(temp_db, capsys):
    from common import sessions as sess
    fake = {"running": True, "status": "idle"}
    with patch.object(sess, "get_session_status", return_value=fake):
        cli_mod.cmd_session_status(_make_args(session_name="task-1"))
    out = capsys.readouterr().out
    assert "task-1" in out
    assert "yes" in out  # running=True -> "yes"


def test_cmd_session_status_stopped(temp_db, capsys):
    from common import sessions as sess
    fake = {"running": False, "status": "stopped"}
    with patch.object(sess, "get_session_status", return_value=fake):
        cli_mod.cmd_session_status(_make_args(session_name="task-1"))
    out = capsys.readouterr().out
    assert "no" in out


def test_cmd_session_status_json(temp_db, capsys):
    from common import sessions as sess
    fake = {"running": True, "status": "idle"}
    with patch.object(sess, "get_session_status", return_value=fake):
        cli_mod.cmd_session_status(_make_args(session_name="task-1", json=True))
    assert json.loads(capsys.readouterr().out) == fake


# ============================================================
# Tests added for coverage of previously-uncovered CLI branches.
# These exercise rendering code paths (status colors, groups, severity icons,
# progress bar edge cases, main() dispatch + unknown-command routing) that
# the happy-path tests above don't touch.
# ============================================================


def test_cmd_list_prs_renders_grouped_pr_lines(temp_db, capsys):
    """cmd_list_prs must print group headers and one line per PR.

    Covers the `for pid, group in groups.items()` branch that walks grouped
    PR data structures into the human output."""
    fake_groups = {
        "proj-a": {
            "name": "Project A",
            "prs": [
                {"number": 101, "status": "open", "title": "Add thing",
                 "ci_status": "passing", "review_status": "approved"},
                {"number": 202, "status": "merged", "title": "Fix other",
                 "ci_status": "failing", "review_status": "changes_requested"},
            ],
        },
    }
    fake = {"groups": fake_groups}
    with patch.object(cli_mod.prs, "list_all_prs", return_value=fake):
        cli_mod.cmd_list_prs(_make_args(status="", search=""))
    out = capsys.readouterr().out
    assert "Project A" in out
    assert "#101" in out and "Add thing" in out
    assert "#202" in out and "Fix other" in out
    assert "passing" in out and "failing" in out


def test_cmd_list_prs_empty_prints_no_prs_message(temp_db, capsys):
    with patch.object(cli_mod.prs, "list_all_prs", return_value={"groups": {}}):
        cli_mod.cmd_list_prs(_make_args(status="", search=""))
    assert "No PRs found" in capsys.readouterr().out


def _seed_events(rows):
    """Insert events directly into the isolated events DB used by conftest."""
    import app_state
    with sqlite3.connect(str(app_state._NOTIF_DB_PATH)) as conn:
        for r in rows:
            conn.execute(
                "INSERT INTO events (id, source, source_id, title, message, "
                "type, severity, url, ts, read, session) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (r["id"], r.get("source", ""), "", r.get("title", ""),
                 r.get("message", ""), r.get("type", "info"),
                 r.get("severity", "info"), "", r["ts"], 0, ""),
            )
        conn.commit()


def test_cmd_events_renders_all_severity_icons(temp_db, capsys):
    """Severity branches: error -> '!', warning -> '*', info -> '-'."""
    _seed_events([
        {"id": "e1", "ts": "2026-04-17T10:00:00Z", "severity": "error",
         "source": "gh", "title": "CI broke", "message": "Build failed"},
        {"id": "e2", "ts": "2026-04-17T11:00:00Z", "severity": "warning",
         "source": "agent", "title": "Needs permission"},
        {"id": "e3", "ts": "2026-04-17T12:00:00Z", "severity": "info",
         "source": "task", "title": "Updated", "message": "x"},
    ])
    cli_mod.cmd_events(_make_args(limit=30, unread_only=False))
    out = capsys.readouterr().out
    assert "CI broke" in out
    assert "Needs permission" in out
    assert "Updated" in out


def test_cmd_events_empty_prints_no_events(temp_db, capsys):
    cli_mod.cmd_events(_make_args(limit=30, unread_only=False))
    out = capsys.readouterr().out
    assert "No events" in out


def test_print_progress_zero_total_noop(capsys):
    """Progress bar with total=0 must early-return without writing anything
    (avoids division-by-zero / NaN rendering)."""
    cli_mod._print_progress("label", 0, 0)
    assert capsys.readouterr().out == ""


def test_print_progress_renders_bar_at_midpoint(capsys):
    """Standard case: half done should emit 50% bar."""
    cli_mod._print_progress("sync", 50, 100)
    out = capsys.readouterr().out
    assert "50/100" in out
    assert "50%" in out
    # Bar char should appear
    assert "#" in out and "-" in out


def test_print_progress_terminator_newline_at_completion(capsys):
    """When current >= total, a trailing newline is written so the next log
    line doesn't overwrite the progress bar."""
    cli_mod._print_progress("sync", 100, 100)
    out = capsys.readouterr().out
    assert out.endswith("\n")


def test_cmd_get_task_renders_ticket_and_notes(temp_db, capsys):
    """get-task should print ticket, notes, type, and PRs when present."""
    # Seed a rich task.
    import server
    server._db.update_task("proj-a", "task-1", ticket_id="JIRA-1",
                            ticket_url="https://j/1", notes="some notes",
                            type="bug")
    server._db.add_pr(project="proj-a", task_id="task-1",
                      number=555, url="https://x/555",
                      title="PR title", status="open")
    cli_mod.cmd_get_task(_make_args(project="proj-a", task_id="task-1"))
    out = capsys.readouterr().out
    assert "JIRA-1" in out
    assert "some notes" in out
    assert "bug" in out
    assert "#555" in out


def test_cmd_get_task_not_found_exits_with_error(temp_db, capsys):
    with pytest.raises(SystemExit) as exc:
        cli_mod.cmd_get_task(_make_args(project="proj-a", task_id="nope"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "not found" in err.lower()


def test_cmd_update_task_no_fields_reports_error(temp_db, capsys):
    """Calling update-task without any field flag must say so rather than
    silently updating nothing. Exit code 1."""
    args = _make_args(project="proj-a", task_id="task-1",
                      status=None, description=None, notes=None,
                      priority=None, ticket_id=None, ticket_url=None,
                      type=None, group=None, follow_ups=None)
    with pytest.raises(SystemExit):
        cli_mod.cmd_update_task(args)
    err = capsys.readouterr().err
    assert "no fields" in err.lower()


# ---- main() dispatch + custom error handler ----

def test_main_no_command_prints_help_and_exits_zero(capsys):
    """`eva-cli` with no args should print help and exit cleanly."""
    with patch.object(sys, "argv", ["eva-cli"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "usage" in out.lower() or "command" in out.lower()


def test_main_help_command_prints_help_and_exits_zero(capsys):
    with patch.object(sys, "argv", ["eva-cli", "help"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
    assert exc.value.code == 0


def test_main_unknown_command_prints_custom_error(capsys):
    """Typing `eva-cli zzz` must exit(1) with a helpful 'Unknown command'
    message on stderr -- NOT the default argparse `usage: ...` spam."""
    with patch.object(sys, "argv", ["eva-cli", "zzz-not-real"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Unknown command" in err
    assert "zzz-not-real" in err
    assert "eva-cli help" in err


def test_update_task_invalid_status_shows_choices(temp_db, capsys):
    """Regression: `--status bogus` used to be swallowed by the
    'Unknown command' handler (which only applies to top-level subcommand
    names, not argument-level `choices=` violations). Now argparse's own
    error path fires, listing every valid status to help the user recover."""
    with patch.object(sys, "argv",
                      ["eva-cli", "update-task", "proj-a", "task-1",
                       "--status", "bogus"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
    assert exc.value.code == 2  # argparse default for usage errors
    err = capsys.readouterr().err
    assert "invalid choice" in err and "bogus" in err
    # Valid choices must be listed so the user can pick one
    assert "not_started" in err and "in_review" in err
    # But not the misleading "Unknown command" prefix.
    assert "Unknown command" not in err


def test_create_task_invalid_type_shows_choices(temp_db, capsys):
    """`--type blahblah` on create-task must surface the canonical type
    list instead of the old "Unknown command" masquerade."""
    with patch.object(sys, "argv",
                      ["eva-cli", "create-task", "proj-a", "new-x",
                       "--type", "blahblah"]):
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err
    # Canonical types that newcomers care about
    assert "feature" in err and "fix" in err
    assert "Unknown command" not in err


def test_update_task_valid_status_accepted(temp_db, capsys):
    """Sanity: the `choices=` addition must not block valid statuses."""
    with patch.object(sys, "argv",
                      ["eva-cli", "update-task", "proj-a", "task-1",
                       "--status", "done"]):
        cli_mod.main()  # should NOT raise
    out = capsys.readouterr().out
    assert "Updated task" in out


def test_cmd_list_sessions_renders_grouped_rows(temp_db, capsys):
    """Covers the main body of `cmd_list_sessions` (line 555-559): group
    header + running/stopped indicator + aligned task_id column."""
    fake = {
        "proj-a": {
            "name": "Project A",
            "sessions": [
                {"task_id": "task-1", "running": True, "status": "idle"},
                {"task_id": "task-2", "running": False, "status": "stopped"},
            ],
        },
    }
    with patch("common.sessions.list_all_sessions", return_value=fake):
        cli_mod.cmd_list_sessions(_make_args())
    out = capsys.readouterr().out
    assert "Project A" in out
    assert "task-1" in out and "running" in out
    assert "task-2" in out and "stopped" in out


def test_dispatch_table_matches_argparse_choices():
    """Every command accepted by argparse must have a dispatch entry.

    This catches a class of bugs where someone adds a new command to the
    argparse parser but forgets to wire up the handler -- which would
    silently hit the `if not handler` fallback and error out for users."""
    parser = cli_mod.build_parser()
    # Walk the subparsers action to extract command choices.
    subparsers_actions = [
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    assert subparsers_actions, "expected argparse to have a subparsers action"
    choices = set(subparsers_actions[0].choices.keys())
    # 'help' is a pseudo-command handled specially in main(); not in dispatch.
    choices.discard("help")
    missing = choices - set(cli_mod._DISPATCH.keys())
    assert not missing, f"commands without dispatch handler: {sorted(missing)}"


def _argparse_schema(parser):
    """Walk an argparse parser and return a JSON-friendly description
    of its subcommands + their args. Used by the snapshot test below
    to lock the CLI surface so unintentional flag renames / additions
    surface as a diff in the snapshot dict."""
    subparsers_actions = [
        a for a in parser._actions
        if isinstance(a, argparse._SubParsersAction)
    ]
    schema = {}
    for sp_action in subparsers_actions:
        for cmd_name, cmd_parser in sp_action.choices.items():
            cmd_schema = {"positional": [], "optional": [], "subcommands": {}}
            for action in cmd_parser._actions:
                if isinstance(action, argparse._HelpAction):
                    continue
                if isinstance(action, argparse._SubParsersAction):
                    # Recurse for nested subparser (e.g. `bench run/list/...`).
                    nested_schema = {}
                    for sub_name, sub_parser in action.choices.items():
                        nested_args = []
                        for sa in sub_parser._actions:
                            if isinstance(sa, argparse._HelpAction):
                                continue
                            if not sa.option_strings:
                                nested_args.append({"kind": "positional", "name": sa.dest, "required": sa.required})
                            else:
                                entry = {"kind": "optional", "flags": sorted(sa.option_strings)}
                                if sa.choices:
                                    entry["choices"] = sorted(sa.choices)
                                nested_args.append(entry)
                        nested_schema[sub_name] = nested_args
                    cmd_schema["subcommands"] = nested_schema
                    continue
                if not action.option_strings:
                    cmd_schema["positional"].append({
                        "name": action.dest, "required": action.required,
                    })
                else:
                    entry = {"flags": sorted(action.option_strings)}
                    if action.choices:
                        entry["choices"] = sorted(action.choices)
                    cmd_schema["optional"].append(entry)
            schema[cmd_name] = cmd_schema
    return schema


# Locked snapshot of the eva-cli argument surface (built 2026-04-26).
# Update DELIBERATELY when you intentionally rename/add/remove a flag --
# diff hits this dict, the test fails, and a code reviewer can confirm
# the change was on purpose. Source of truth still lives in `build_parser`;
# this just guards against accidental drift (forgotten alias, typo'd
# choice, missing positional, etc.).
_EXPECTED_CLI_SCHEMA_KEYS = frozenset({
    "list-projects", "update-project",
    "list-tasks", "fetch-tasks", "get-task", "create-task", "update-task",
    "rename-task", "close-task", "delete-task", "check-status",
    "add-dep", "remove-dep", "append-history", "list-history",
    "list-prs", "add-pr", "remove-pr", "sync-prs", "pr-detail",
    "list-sessions", "open-session", "open-review-session", "send-message",
    "kill-session", "resume-session", "session-status",
    "stats", "usage", "pr-stats", "events",
    "auth-status", "renew-cert", "audit",
    "append-review-history", "list-review-history", "update-review",
    "help",
})


def test_cli_argument_schema_top_level_commands():
    """Snapshot: every core top-level subcommand is enumerated, and a
    removal flags a regression so agent scripts don't silently break.

    Extensions can contribute additional subcommands via the
    `<ext>/src/cli.py` addon hook -- those are NOT included in the
    snapshot (the OSS-shipping test mustn't be coupled to any
    particular extension), so we assert the snapshot is a SUBSET of
    `actual`, not equality."""
    parser = cli_mod.build_parser()
    schema = _argparse_schema(parser)
    actual = set(schema)
    missing = _EXPECTED_CLI_SCHEMA_KEYS - actual
    assert not missing, f"core commands removed from CLI: {sorted(missing)}"


def test_cli_status_choices_locked_to_canonical_set():
    """Regression guard: --status on update-task/create-task must
    accept exactly the 7 canonical statuses. Source of truth is
    `eva_db.VALID_STATUSES`. CLI choices are sourced from the same
    set; this test catches accidental hardcoding of a divergent list."""
    parser = cli_mod.build_parser()
    schema = _argparse_schema(parser)
    # `blocked` deliberately NOT here -- it's computed, never persisted.
    expected = {"not_started", "in_progress", "in_review",
                "done", "needs_follow_up", "closed"}
    for cmd in ("update-task", "create-task"):
        opts = schema[cmd]["optional"]
        status_opt = next((o for o in opts if "--status" in o["flags"]), None)
        assert status_opt is not None, f"{cmd} lost --status flag"
        assert set(status_opt["choices"]) == expected, (
            f"{cmd} --status choices drifted from canonical: "
            f"got {sorted(status_opt['choices'])}"
        )


def test_cli_type_choices_accept_canonical_and_aliases():
    """`--type` accepts 9 canonical values + 3 conventional-commit
    aliases. The boundary in `common.tasks.canonicalize_task_type`
    rewrites aliases to canonical at write time, so the DB only
    stores the 9 canonical spellings. This test locks down the
    surface so a future refactor doesn't quietly drop alias support
    (UX regression) or add new aliases without updating the audit."""
    parser = cli_mod.build_parser()
    schema = _argparse_schema(parser)
    expected_canonical = {"feature", "fix", "test", "refactor", "docs",
                          "chore", "perf", "benchmark", "cleanup"}
    expected_aliases = {"feat", "doc", "bug"}
    expected = expected_canonical | expected_aliases
    for cmd in ("update-task", "create-task"):
        opts = schema[cmd]["optional"]
        type_opt = next((o for o in opts if "--type" in o["flags"]), None)
        assert type_opt is not None, f"{cmd} lost --type flag"
        assert set(type_opt["choices"]) == expected, (
            f"{cmd} --type choices drifted: "
            f"got {sorted(type_opt['choices'])}, expected {sorted(expected)}"
        )

    # Cross-check: the alias set CLI accepts must match the boundary's
    # alias map, otherwise the CLI accepts an alias the boundary
    # doesn't know how to canonicalise.
    from common.tasks import TASK_TYPE_ALIASES
    assert set(TASK_TYPE_ALIASES.keys()) == expected_aliases, (
        "CLI alias list and common.tasks.TASK_TYPE_ALIASES must agree"
    )


def test_frontend_taskstatus_union_matches_backend_canonical():
    """Two layers must agree on the canonical status set, with one
    documented exception:

      Stored: `eva_db.VALID_STATUSES` (sqlite CHECK enforced + CLI
              argparse `--status` choices, all sourced from this).
      Displayed: `frontend/src/types.ts::TaskStatus` union (Stored
              + 'blocked', because `blocked` is a *computed* display
              status -- never written, derived live from dep graph).

    Frontend MUST contain every stored status (else API responses
    won't render) and MUST contain 'blocked' (else effective_status
    of a blocked task can't render).
    """
    import re
    from eva_db import VALID_STATUSES
    types_ts = open(os.path.join(
        _REPO_ROOT, "frontend", "src", "types.ts")).read()
    m = re.search(
        r"export\s+type\s+TaskStatus\s*=\s*([^\n]+)", types_ts)
    assert m, "couldn't locate TaskStatus union in frontend types.ts"
    frontend_set = set(re.findall(r"'([^']+)'", m.group(1)))
    expected = VALID_STATUSES | {"blocked"}
    assert frontend_set == expected, (
        f"backend stored vs frontend displayed status drift:\n"
        f"  only in backend: {sorted(VALID_STATUSES - frontend_set)}\n"
        f"  only in frontend (excl. 'blocked'): "
        f"{sorted(frontend_set - VALID_STATUSES - {'blocked'})}\n"
        f"  expected frontend == backend U {{'blocked'}}"
    )
    # Storage layer MUST NOT contain 'blocked' -- it's purely computed.
    assert "blocked" not in VALID_STATUSES, (
        "VALID_STATUSES must not include 'blocked' -- it's a computed "
        "display status, never persisted. See eva_db.UNBLOCKING_DEP_STATUSES."
    )


def test_frontend_unblocking_set_matches_backend():
    """`eva_db.UNBLOCKING_DEP_STATUSES` and the frontend's
    `UNBLOCKING_DEP_STATUSES` ReadonlySet must agree -- they encode
    the *same* "which dep states unblock the dependent" rule across
    the Py/TS boundary."""
    import re
    from eva_db import UNBLOCKING_DEP_STATUSES
    helpers_ts = open(os.path.join(
        _REPO_ROOT, "frontend", "src",
        "utils", "taskHelpers.ts")).read()
    m = re.search(
        r"UNBLOCKING_DEP_STATUSES[^=]*=\s*new Set\(\[([^\]]+)\]\)",
        helpers_ts,
    )
    assert m, "couldn't locate UNBLOCKING_DEP_STATUSES in taskHelpers.ts"
    frontend_set = set(re.findall(r"'([^']+)'", m.group(1)))
    assert frontend_set == set(UNBLOCKING_DEP_STATUSES), (
        f"backend UNBLOCKING_DEP_STATUSES vs frontend drift:\n"
        f"  only in backend: {sorted(UNBLOCKING_DEP_STATUSES - frontend_set)}\n"
        f"  only in frontend: {sorted(frontend_set - UNBLOCKING_DEP_STATUSES)}"
    )


# ============================================================
# append-history / list-history
# ============================================================

class TestAppendHistory:
    def test_basic_append(self, temp_db, capsys):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="task-1", text="finished impl")
        cli_mod.cmd_append_history(args)
        out = capsys.readouterr().out
        assert "history+" in out
        assert "finished impl" in out
        # DB state
        hist = temp_db.list_task_history("proj-a", "task-1")
        assert hist and hist[0]["text"] == "finished impl"

    def test_json_mode_returns_entry(self, temp_db, capsys):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="task-1", text="hi", json=True)
        cli_mod.cmd_append_history(args)
        out = capsys.readouterr().out
        d = json.loads(out)
        assert d["text"] == "hi"
        assert d["ts"]

    def test_empty_text_exits_with_error(self, temp_db):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="task-1", text="   ")
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_append_history(args)
        assert exc.value.code == 1

    def test_over_100_chars_exits_with_helpful_hint(self, temp_db, capsys):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="task-1", text="x" * 120)
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_append_history(args)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        # Hint should mention length + suggest where verbose context goes.
        assert "100" in err
        assert "PR description" in err or "design doc" in err

    def test_unknown_task_exits_with_hint(self, temp_db, capsys):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="ghost", text="hi")
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_append_history(args)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        # Hint points the user at list-tasks to discover the right id.
        assert "list-tasks" in err


class TestListPrsProjectFilter:
    """--project flag scopes list-prs to a single project client-side."""

    def _seed_prs(self, db):
        db.add_pr("proj-a", "task-1", number=100, url="x", status="open", title="a1")
        db.add_pr("proj-a", "task-1", number=101, url="y", status="open", title="a2")
        # Post-Phase-2 merge: task_id is globally unique across
        # projects, so the proj-b task uses a distinct id.
        db.create_task("proj-b", "task-1b", description="b task")
        db.add_pr("proj-b", "task-1b", number=200, url="z", status="open", title="b1")

    def test_filter_returns_only_matching_project(self, temp_db, capsys):
        self._seed_prs(temp_db)
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", status="", search="", json=True)
        cli_mod.cmd_list_prs(args)
        d = json.loads(capsys.readouterr().out)
        assert list(d["groups"].keys()) == ["proj-a"]
        nums = [p["number"] for p in d["groups"]["proj-a"]["prs"]]
        assert set(nums) == {100, 101}

    def test_filter_unknown_project_is_empty(self, temp_db, capsys):
        self._seed_prs(temp_db)
        cli_mod._ensure_core()
        args = _make_args(project="ghost", status="", search="", json=True)
        cli_mod.cmd_list_prs(args)
        d = json.loads(capsys.readouterr().out)
        assert d["groups"] == {}

    def test_no_filter_returns_all_projects(self, temp_db, capsys):
        self._seed_prs(temp_db)
        cli_mod._ensure_core()
        args = _make_args(project="", status="", search="", json=True)
        cli_mod.cmd_list_prs(args)
        d = json.loads(capsys.readouterr().out)
        assert {"proj-a", "proj-b"}.issubset(d["groups"].keys())


class TestListHistory:
    def test_empty_history_prints_placeholder(self, temp_db, capsys):
        cli_mod._ensure_core()
        args = _make_args(project="proj-a", task_id="task-1")
        cli_mod.cmd_list_history(args)
        out = capsys.readouterr().out
        assert "no history yet" in out

    def test_prints_entries_newest_first(self, temp_db, capsys):
        cli_mod._ensure_core()
        temp_db.append_task_history("proj-a", "task-1", "first", ts="2026-04-20T10:00:00Z")
        temp_db.append_task_history("proj-a", "task-1", "second", ts="2026-04-20T11:00:00Z")
        args = _make_args(project="proj-a", task_id="task-1", limit=50)
        cli_mod.cmd_list_history(args)
        out = capsys.readouterr().out
        # Both entries present; newest ("second") appears before oldest ("first").
        assert out.index("second") < out.index("first")

    def test_json_mode(self, temp_db, capsys):
        cli_mod._ensure_core()
        temp_db.append_task_history("proj-a", "task-1", "only")
        args = _make_args(project="proj-a", task_id="task-1", json=True, limit=50)
        cli_mod.cmd_list_history(args)
        out = capsys.readouterr().out
        d = json.loads(out)
        assert len(d["history"]) == 1
        assert d["history"][0]["text"] == "only"

    def test_honors_limit(self, temp_db, capsys):
        cli_mod._ensure_core()
        for i in range(5):
            temp_db.append_task_history("proj-a", "task-1", f"e{i}")
        args = _make_args(project="proj-a", task_id="task-1", limit=2)
        cli_mod.cmd_list_history(args)
        out = capsys.readouterr().out
        # Exactly two entries printed -> exactly two timestamp markers "["
        assert out.count("[") == 2


# ---- Error-hint quality ----
#
# These tests pin down the "friendly hint on error" guarantee: when a user
# gives a bad project/task id, the CLI shouldn't just say "not found" -- it
# should also tell them how to list valid ids. That matters for anyone
# (human or agent) driving eva-cli without having memorised the schema.

class TestErrorHints:
    def test_strip_ansi_removes_sgr(self):
        """_strip_ansi is the table formatter's width helper. Regression: it
        used to be inlined twice with subtle copy-paste risk."""
        colored = "\033[31mhello\033[0m"
        assert cli_mod._strip_ansi(colored) == "hello"
        # Nested codes too.
        nested = "\033[1m\033[32mok\033[0m\033[0m"
        assert cli_mod._strip_ansi(nested) == "ok"
        # No-op on plain text.
        assert cli_mod._strip_ansi("plain") == "plain"

    def test_err_project_not_found_includes_list_hint(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_mod._err_project_not_found("zz-bad-proj")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "zz-bad-proj" in err
        assert "list-projects" in err

    def test_err_task_not_found_includes_list_tasks_hint(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli_mod._err_task_not_found("proj-a", "nope")
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "nope" in err and "proj-a" in err
        assert "list-tasks proj-a" in err

    def test_cmd_get_task_not_found_emits_hint(self, temp_db, capsys):
        """Integration: cmd_get_task on a bad id must surface the hint via
        the shared helper, not just the bare 'not found' string."""
        with pytest.raises(SystemExit):
            cli_mod.cmd_get_task(_make_args(project="proj-a", task_id="missing-id"))
        err = capsys.readouterr().err
        assert "missing-id" in err
        assert "list-tasks proj-a" in err

    def test_cmd_list_tasks_bad_project_emits_hint(self, temp_db, capsys):
        with pytest.raises(SystemExit):
            cli_mod.cmd_list_tasks(_make_args(project="not-a-real-proj"))
        err = capsys.readouterr().err
        assert "not-a-real-proj" in err
        assert "list-projects" in err

    def test_cmd_remove_pr_not_found_mentions_task(self, temp_db, capsys):
        """When the PR is unknown on a known task, the error should point
        at how to inspect the task, not just say 'PR not found'."""
        cli_mod._ensure_core()
        with pytest.raises(SystemExit):
            cli_mod.cmd_remove_pr(_make_args(
                project="proj-a", task_id="task-1", number=99999))
        err = capsys.readouterr().err
        assert "#99999" in err
        assert "get-task proj-a task-1" in err


# ============================================================
# Review commands (append/list/update-review). These are PR-URL keyed
# and meant for the agent to call from a review session, so the output
# format matters -- keep it terse and deterministic.
# ============================================================


def _seed_review_pr_in_temp_db(db, url="https://github.com/example/repo/pull/42"):
    db.upsert_review_pr(
        url=url, repo="example/repo", number=42,
        title="Test review PR", author="somebody-else",
        status="open", last_updated="", source="github",
    )
    return url


def test_cmd_append_review_history_happy(temp_db, capsys):
    """append-review-history inserts an entry and prints the timestamp."""
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    cli_mod.cmd_append_review_history(_make_args(
        url=url, text="skimmed, LGTM", source="manual"))
    out = capsys.readouterr().out
    assert "review-history+" in out
    assert "skimmed, LGTM" in out


def test_cmd_append_review_history_rejects_missing_review(temp_db, capsys):
    """Unknown URL -> error mentions the URL plus a hint pointing at the
    queue (you can only write history to PRs currently in review_prs)."""
    cli_mod._ensure_core()
    with pytest.raises(SystemExit):
        cli_mod.cmd_append_review_history(_make_args(
            url="https://github.com/nope/nope/pull/1",
            text="hi", source="manual"))
    err = capsys.readouterr().err
    assert "not found" in err
    assert "review queue" in err or "review_prs" in err


def test_cmd_append_review_history_rejects_too_long(temp_db, capsys):
    """>100 chars -> error with a 'trim to one line' hint."""
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    with pytest.raises(SystemExit):
        cli_mod.cmd_append_review_history(_make_args(
            url=url, text="x" * 200, source="manual"))
    err = capsys.readouterr().err
    assert "chars" in err


def test_cmd_append_review_history_rejects_empty_text(temp_db, capsys):
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    with pytest.raises(SystemExit):
        cli_mod.cmd_append_review_history(_make_args(
            url=url, text="   ", source="manual"))
    err = capsys.readouterr().err
    assert "empty" in err


def test_cmd_list_review_history_shows_entries(temp_db, capsys):
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    temp_db.append_review_history(url, "first", ts="2026-04-24T10:00:00Z")
    temp_db.append_review_history(url, "second", ts="2026-04-24T11:00:00Z")
    cli_mod.cmd_list_review_history(_make_args(url=url, limit=50))
    out = capsys.readouterr().out
    assert "first" in out
    assert "second" in out
    # Newest-first ordering preserved in CLI output.
    assert out.find("second") < out.find("first")


def test_cmd_list_review_history_empty_shows_placeholder(temp_db, capsys):
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    cli_mod.cmd_list_review_history(_make_args(url=url, limit=50))
    out = capsys.readouterr().out
    assert "no review history" in out


def test_cmd_update_review_flips_workflow_state(temp_db, capsys):
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    cli_mod.cmd_update_review(_make_args(url=url, state="done"))
    out = capsys.readouterr().out
    assert "done" in out
    assert temp_db.get_review_pr(url)["my_workflow_state"] == "done"


def test_cmd_update_review_missing_url_errors(temp_db, capsys):
    cli_mod._ensure_core()
    with pytest.raises(SystemExit):
        cli_mod.cmd_update_review(_make_args(
            url="https://github.com/nope/nope/pull/1", state="done"))
    err = capsys.readouterr().err
    assert "not found" in err.lower()



def test_cmd_append_review_history_json_output(temp_db, capsys):
    """--json lets agent parse the result programmatically."""
    cli_mod._ensure_core()
    url = _seed_review_pr_in_temp_db(temp_db)
    cli_mod.cmd_append_review_history(_make_args(
        url=url, text="ok", source="agent", json=True))
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["text"] == "ok"
    assert data["source"] == "agent"


# ============================================================
# cmd_audit unit tests -- direct calls, mock audit
# ============================================================


def _audit_args(**overrides):
    base = {
        "json": False, "fix": False, "kind": None,
        "severity": None, "list_kinds": False,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def test_cmd_audit_clean_db_prints_clean_message(temp_db, capsys):
    """Empty findings list -> headline + 'No data-integrity issues found.'.
    Exit code 0 (no error severity)."""
    with patch("common.audit.run_audit", return_value={
        "findings": [],
        "summary": {"total": 0, "by_severity": {}, "by_kind": {}},
    }):
        cli_mod.cmd_audit(_audit_args())
    out = capsys.readouterr().out
    assert "0 finding(s)" in out
    assert "No data-integrity issues found" in out


def test_cmd_audit_kind_filter_shows_n_of_total(temp_db, capsys):
    """`--kind X` headline reads 'N of TOTAL match kind=X' so the user
    knows the displayed list is filtered, not the global state."""
    findings = [
        {"kind": "stale_task_session", "severity": "warn", "message": "m1",
         "fixable": False},
        {"kind": "orphan_pr", "severity": "info", "message": "m2",
         "fixable": True},
    ]
    with patch("common.audit.run_audit", return_value={
        "findings": findings,
        "summary": {"total": 2, "by_severity": {"warn": 1, "info": 1},
                    "by_kind": {"stale_task_session": 1, "orphan_pr": 1}},
    }):
        cli_mod.cmd_audit(_audit_args(kind="stale_task_session"))
    out = capsys.readouterr().out
    assert "1 of 2 finding(s) match kind=stale_task_session" in out
    # Only the stale_task_session message is rendered.
    assert "m1" in out
    assert "m2" not in out


def test_cmd_audit_unknown_kind_errors_loudly(temp_db, capsys):
    """Pre-validation: a typo in `--kind` errors with a hint that
    points at `--list-kinds` instead of silently filtering to zero
    findings (the old behaviour, which read as `all clean`)."""
    with pytest.raises(SystemExit):
        cli_mod.cmd_audit(_audit_args(kind="nope_typo"))
    err = capsys.readouterr().err
    assert "unknown audit kind" in err
    assert "list-kinds" in err


def test_cmd_audit_unknown_severity_errors_loudly(temp_db, capsys):
    """Same defence for `--severity`: invalid value -> hard exit with
    the valid set listed."""
    with pytest.raises(SystemExit):
        cli_mod.cmd_audit(_audit_args(severity="critical"))
    err = capsys.readouterr().err
    assert "unknown audit severity" in err
    # Helpful hint enumerates the valid options.
    assert "info" in err and "warn" in err and "error" in err


def test_cmd_audit_multi_kind_filter(temp_db, capsys):
    """`--kind` is repeatable -- pass a list of kinds and the filter
    keeps any finding whose kind is in the union."""
    findings = [
        {"kind": "orphan_pr", "severity": "warn", "message": "m1",
         "fixable": True},
        {"kind": "orphan_history", "severity": "warn", "message": "m2",
         "fixable": True},
        {"kind": "duplicate_pr_url", "severity": "info", "message": "m3",
         "fixable": False},
    ]
    with patch("common.audit.run_audit", return_value={
        "findings": findings,
        "summary": {"total": 3, "by_severity": {"warn": 2, "info": 1},
                    "by_kind": {"orphan_pr": 1, "orphan_history": 1,
                                "duplicate_pr_url": 1}},
    }):
        cli_mod.cmd_audit(_audit_args(
            kind=["orphan_pr", "orphan_history"],
        ))
    out = capsys.readouterr().out
    # Headline shows both kinds in the active filter line.
    assert "2 of 3 finding(s) match" in out
    assert "orphan_pr" in out and "orphan_history" in out
    assert "m1" in out and "m2" in out
    # Non-matching duplicate_pr_url message is suppressed.
    assert "m3" not in out


def test_cmd_audit_severity_filter(temp_db, capsys):
    """`--severity error` keeps only error-severity findings."""
    findings = [
        {"kind": "orphan_pr", "severity": "warn", "message": "warn-line",
         "fixable": True},
        {"kind": "audit_check_error", "severity": "error",
         "message": "err-line", "fixable": False},
    ]
    with patch("common.audit.run_audit", return_value={
        "findings": findings,
        "summary": {"total": 2, "by_severity": {"warn": 1, "error": 1},
                    "by_kind": {"orphan_pr": 1, "audit_check_error": 1}},
    }):
        # `error` severity makes the CLI exit 2; catch it.
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_audit(_audit_args(severity="error"))
        assert exc.value.code == 2
    out = capsys.readouterr().out
    assert "1 of 2 finding(s) match severity=error" in out
    assert "err-line" in out
    assert "warn-line" not in out


def test_cmd_audit_combined_kind_severity_filter(temp_db, capsys):
    """Both `--kind` and `--severity` can compose -- intersection."""
    findings = [
        {"kind": "orphan_pr", "severity": "warn", "message": "warn-orphan",
         "fixable": True},
        {"kind": "orphan_pr", "severity": "info", "message": "info-orphan",
         "fixable": True},
        {"kind": "duplicate_pr_url", "severity": "warn",
         "message": "warn-dup", "fixable": False},
    ]
    with patch("common.audit.run_audit", return_value={
        "findings": findings,
        "summary": {"total": 3, "by_severity": {"warn": 2, "info": 1},
                    "by_kind": {"orphan_pr": 2, "duplicate_pr_url": 1}},
    }):
        cli_mod.cmd_audit(_audit_args(
            kind="orphan_pr", severity="warn",
        ))
    out = capsys.readouterr().out
    assert "1 of 3 finding(s) match kind=orphan_pr  severity=warn" in out
    assert "warn-orphan" in out
    assert "info-orphan" not in out
    assert "warn-dup" not in out


def test_cmd_audit_list_kinds_prints_enum(temp_db, capsys):
    """`--list-kinds` enumerates KNOWN_KINDS + KNOWN_SEVERITIES and
    exits without running an audit scan. No mock needed because
    run_audit must NOT be called."""
    with patch("common.audit.run_audit") as mock_run:
        cli_mod.cmd_audit(_audit_args(list_kinds=True))
    mock_run.assert_not_called()
    out = capsys.readouterr().out
    # A few representative kinds + every severity.
    assert "orphan_pr" in out
    assert "stale_task_session" in out
    assert "info" in out and "warn" in out and "error" in out


def test_cmd_audit_list_kinds_json(temp_db, capsys):
    """`--list-kinds --json` emits the structured envelope."""
    with patch("common.audit.run_audit") as mock_run:
        cli_mod.cmd_audit(_audit_args(list_kinds=True, json=True))
    mock_run.assert_not_called()
    body = json.loads(capsys.readouterr().out)
    assert "kinds" in body and "severities" in body
    assert "orphan_pr" in body["kinds"]
    assert set(body["severities"]) == {"info", "warn", "error"}


def test_cmd_audit_fix_flag_runs_fixer_then_rescans(temp_db, capsys):
    """`--fix` calls `common.audit.fix_all`, then re-runs `run_audit` so
    the user sees the post-fix state. Headline includes
    `(fixed=N skipped=N)`."""
    pre_findings = [
        {"kind": "orphan_pr", "severity": "warn", "message": "m1",
         "fixable": True, "ref": {}},
    ]
    post_findings = []  # everything fixed
    run_audit_mock = MagicMock(side_effect=[
        {"findings": pre_findings,
         "summary": {"total": 1, "by_severity": {"warn": 1},
                     "by_kind": {"orphan_pr": 1}}},
        {"findings": post_findings,
         "summary": {"total": 0, "by_severity": {}, "by_kind": {}}},
    ])
    with patch("common.audit.run_audit", run_audit_mock), \
         patch("common.audit.fix_all", return_value={"fixed": 1, "skipped": 0}):
        cli_mod.cmd_audit(_audit_args(fix=True))
    out = capsys.readouterr().out
    assert "fixed=1" in out
    assert "skipped=0" in out
    # Headline now reflects the post-fix state (0 findings).
    assert "0 finding(s)" in out
    assert run_audit_mock.call_count == 2


def test_cmd_audit_error_severity_exits_non_zero(temp_db):
    """An `error`-severity finding makes the CLI exit 2 so the user can
    wire `eva-cli audit` into CI / git hooks and fail loud."""
    with patch("common.audit.run_audit", return_value={
        "findings": [{"kind": "broken_state", "severity": "error",
                      "message": "x", "fixable": False}],
        "summary": {"total": 1, "by_severity": {"error": 1},
                    "by_kind": {"broken_state": 1}},
    }):
        with pytest.raises(SystemExit) as exc:
            cli_mod.cmd_audit(_audit_args())
        assert exc.value.code == 2


def test_cmd_audit_json_emits_structured_envelope(temp_db, capsys):
    """`--json` short-circuits the human render path; output is one
    valid JSON envelope with `findings` + `summary` (+ `fixed` when
    --fix ran)."""
    with patch("common.audit.run_audit", return_value={
        "findings": [{"kind": "x", "severity": "info", "message": "m",
                      "fixable": False}],
        "summary": {"total": 1, "by_severity": {"info": 1},
                    "by_kind": {"x": 1}},
    }):
        cli_mod.cmd_audit(_audit_args(json=True))
    body = json.loads(capsys.readouterr().out)
    assert body["findings"][0]["kind"] == "x"
    assert body["summary"]["total"] == 1
    assert body["fixed"] is None  # --fix wasn't passed


# ============================================================
# eva-cli bootstrap: Python-version bail-out
# ============================================================


def test_eva_cli_bails_helpfully_on_old_python_via_source_inspection():
    """The CLI uses PEP-585 / PEP-604 generics throughout the codebase,
    so on Python <3.9 it can't even reach the `_error` helper. The
    bootstrap therefore checks `sys.version_info` *first* and prints
    a one-line hint pointing at EVA_VENV.

    We verify this contract by source-inspecting the bail block
    rather than spawning a real old interpreter (which the test host
    may not have). The presence + shape of the check is what matters
    -- it's the difference between an unhelpful SyntaxError and a
    user-actionable message for OSS contributors on stock distros."""
    cli_path = os.path.join(_REPO_ROOT, "eva-cli")
    with open(cli_path) as f:
        src = f.read()
    # Bail comes before the project-root insertion so the import
    # chain in eva_db.py never gets reached on old Python.
    bail_idx = src.find("sys.version_info < (3, 9)")
    sys_path_idx = src.find("sys.path.insert(0, ")
    assert bail_idx > 0, (
        "eva-cli must check sys.version_info < (3, 9) at bootstrap"
    )
    assert sys_path_idx > 0, (
        "eva-cli must add a source dir to sys.path at bootstrap"
    )
    assert bail_idx < sys_path_idx, (
        "Python-version bail must run before sys.path manipulation; "
        "otherwise eva_db.py import will SyntaxError first."
    )
    # The hint must mention EVA_VENV so the user knows the escape hatch.
    bail_block = src[bail_idx:sys_path_idx]
    assert "EVA_VENV" in bail_block, (
        "Bail message should point at the EVA_VENV env var"
    )
