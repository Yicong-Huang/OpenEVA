import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_build_background_basic():
    from server import build_background

    task_data = {
        "task_id": "fix-arrow-cast",
        "description": "Fix Arrow type casting for numpy < 2.0",
        "status": "in_progress",
        "ticket_id": "EX-55390",
        "ticket_url": "https://issues.example.org/jira/browse/EX-55390",
        "dependencies": ["add-arrow-serializer"],
        "prs": [
            {"number": 54296, "status": "open", "title": "Fix arrow cast",
             "url": "https://github.com/example/repo/pull/54296"}
        ],
    }
    dep_statuses = {"add-arrow-serializer": "done"}
    prompt_template = "Fix the CI failures on this PR. Analyze the CI log, find root cause, provide fix."
    project_name = "Example Serializer Refactor"

    result = build_background(task_data, project_name, prompt_template, dep_statuses)

    assert "[Background]" in result
    assert "fix-arrow-cast" in result
    assert "EX-55390" in result
    assert "add-arrow-serializer(done)" in result
    assert "https://github.com/example/repo/pull/54296" in result
    assert "[Action]" in result
    assert "Fix the CI" in result


def test_build_background_with_pr_context():
    from server import build_background

    task_data = {
        "task_id": "fix-arrow-cast",
        "description": "Fix Arrow type casting",
        "status": "in_progress",
        "ticket_id": None,
        "ticket_url": None,
        "dependencies": [],
        "prs": [
            {"number": 54296, "status": "open", "title": "Fix arrow cast",
             "head_branch": "EX-55390", "ci_status": "failure",
             "review_status": "changes_requested"}
        ],
    }
    prompt_template = "Fix the CI failures on this PR."
    pr_context = {"number": 54296, "repo": "example/repo"}

    result = build_background(
        task_data, "Test Project", prompt_template, {},
        pr_context=pr_context,
    )

    assert "Focus PR #54296:" in result
    assert "example/repo" in result


def test_build_background_no_ticket_no_deps():
    from server import build_background

    task_data = {
        "task_id": "simple-task",
        "description": "A simple task",
        "status": "not_started",
        "ticket_id": None,
        "ticket_url": None,
        "dependencies": [],
        "prs": [],
    }

    result = build_background(task_data, "Proj", "Do this task.", {})
    assert "[Background]" in result
    assert "simple-task" in result
    assert "Ticket:" not in result
    assert "Deps:" not in result
    assert "[Action]" in result


def test_build_background_empty_description():
    """build_background should not error with an empty description."""
    from server import build_background

    task_data = {
        "task_id": "empty-desc-task",
        "description": "",
        "status": "not_started",
        "ticket_id": None,
        "ticket_url": None,
        "dependencies": [],
        "prs": [],
    }

    result = build_background(task_data, "Proj", "Do the work.", {})
    assert "[Background]" in result
    assert "empty-desc-task" in result
    assert "[Action]" in result


def test_build_background_multiple_prs():
    """build_background with multiple PRs should list all of them with URLs."""
    from server import build_background

    task_data = {
        "task_id": "multi-pr-task",
        "description": "Task with multiple PRs",
        "status": "in_review",
        "ticket_id": None,
        "ticket_url": None,
        "dependencies": [],
        "prs": [
            {"number": 101, "status": "merged", "title": "PR one",
             "url": "https://github.com/example/repo/pull/101"},
            {"number": 202, "status": "open", "title": "PR two",
             "url": "https://github.com/example/repo/pull/202"},
            {"number": 303, "status": "draft", "title": "PR three",
             "url": "https://github.com/example/repo/pull/303"},
        ],
    }

    result = build_background(task_data, "Test Project", "Review all PRs.", {})
    assert "https://github.com/example/repo/pull/101" in result
    assert "https://github.com/example/repo/pull/202" in result
    assert "https://github.com/example/repo/pull/303" in result


def test_build_background_pr_context_no_match():
    """pr_context with a number not in prs list should produce no PR Detail section."""
    from server import build_background

    task_data = {
        "task_id": "mismatch-task",
        "description": "Task with one PR",
        "status": "in_progress",
        "ticket_id": None,
        "ticket_url": None,
        "dependencies": [],
        "prs": [
            {"number": 999, "status": "open", "title": "The PR",
             "url": "https://github.com/example/repo/pull/999"},
        ],
    }
    # pr_context refers to number 555 which is not in the prs list
    pr_context = {"number": 555, "repo": "example/repo"}

    result = build_background(task_data, "Proj", "Fix it.", {}, pr_context=pr_context)
    assert "Focus PR" not in result
