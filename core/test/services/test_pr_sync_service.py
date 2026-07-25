"""Tests for the pr_sync_service scheduler jobs.

Two per-tick functions the scheduler calls:
  - sync_task_prs_dirty_once: consumes dirty flags set by the notification
    poller, batch-refreshes those PRs, clears the flag on success.
  - sync_task_prs_full_once: backstop that refreshes all open task PRs.

We test the tick bodies directly with app_state.gh_run* and the batch
helper mocked -- no real gh, no scheduler, no sleep.
"""

from unittest.mock import patch, MagicMock


# ---- sync_task_prs_dirty_once ----

def test_dirty_tick_no_dirty_prs_makes_no_gh_call():
    """No dirty PRs -> early return, batch refresh never invoked."""
    import services.pr_sync_service as svc
    with patch("app_state._db") as mock_db, \
         patch.object(svc, "_batch_refresh_prs_via_graphql") as mock_batch:
        mock_db.list_dirty_prs.return_value = []
        svc.sync_task_prs_dirty_once()
    mock_batch.assert_not_called()


def test_dirty_tick_refreshes_and_clears_succeeded():
    """Dirty PRs -> batch refresh called with their {number,url}; only the
    PRs the batch resolved get their dirty flag cleared."""
    import services.pr_sync_service as svc
    dirty = [
        {"number": 10, "url": "https://github.com/o/r/pull/10"},
        {"number": 20, "url": "https://github.com/o/r/pull/20"},
    ]
    with patch("app_state._db") as mock_db, \
         patch.object(svc, "_batch_refresh_prs_via_graphql") as mock_batch:
        mock_db.list_dirty_prs.return_value = dirty
        # batch resolved only #10; #20 fell through.
        mock_batch.return_value = (1, {10})
        svc.sync_task_prs_dirty_once()

    args, _ = mock_batch.call_args
    passed = args[0]
    assert {p["number"] for p in passed} == {10, 20}
    mock_db.clear_pr_dirty.assert_called_once_with(10)


# ---- sync_task_prs_full_once ----

def test_full_tick_no_open_prs_makes_no_gh_call():
    """No open PRs -> early return, batch refresh never invoked."""
    import services.pr_sync_service as svc
    with patch("app_state._db") as mock_db, \
         patch.object(svc, "_batch_refresh_prs_via_graphql") as mock_batch:
        mock_db.list_all_prs.return_value = []
        svc.sync_task_prs_full_once()
    mock_batch.assert_not_called()


def test_full_tick_refreshes_open_prs():
    """Open PRs -> batch refresh called with the open set; queried with
    status='open' so closed/merged PRs are excluded."""
    import services.pr_sync_service as svc
    open_prs = [
        {"number": 5, "url": "https://github.com/o/r/pull/5"},
        {"number": 6, "url": "https://github.com/o/r/pull/6"},
    ]
    with patch("app_state._db") as mock_db, \
         patch.object(svc, "_batch_refresh_prs_via_graphql") as mock_batch:
        mock_db.list_all_prs.return_value = open_prs
        mock_batch.return_value = (2, {5, 6})
        svc.sync_task_prs_full_once()

    mock_db.list_all_prs.assert_called_once_with(status="open")
    args, _ = mock_batch.call_args
    assert {p["number"] for p in args[0]} == {5, 6}


# ---- error handling (scheduler must never see an exception) ----

def test_dirty_tick_swallows_errors(capsys):
    import services.pr_sync_service as svc
    with patch("app_state._db") as mock_db:
        mock_db.list_dirty_prs.side_effect = RuntimeError("sim failure")
        svc.sync_task_prs_dirty_once()
    out = capsys.readouterr().out
    assert "sim failure" in out


def test_full_tick_swallows_errors(capsys):
    import services.pr_sync_service as svc
    with patch("app_state._db") as mock_db:
        mock_db.list_all_prs.side_effect = RuntimeError("sim failure")
        svc.sync_task_prs_full_once()
    out = capsys.readouterr().out
    assert "sim failure" in out
