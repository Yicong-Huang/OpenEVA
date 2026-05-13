"""Extra tests for routes/common.prs.py -- cover endpoints not hit by test_prs_routes.py.

Covers: pr-diff, pr-title, pr-comment-reply, pr-comment-edit, pr-thread-resolve,
pr-lookup, pr-refresh, all-prs (GET with params), all-prs/sync (POST).
"""

import json
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# GET /api/pr-diff
# ---------------------------------------------------------------------------

class TestPrDiff:
    DIFF_OUTPUT = (
        "diff --git a/README.md b/README.md\n"
        "--- a/README.md\n"
        "+++ b/README.md\n"
        "@@ -1,3 +1,4 @@\n"
        " line1\n"
        "+new line\n"
        " line2\n"
        "diff --git a/src/main.py b/src/main.py\n"
        "--- a/src/main.py\n"
        "+++ b/src/main.py\n"
        "@@ -10,6 +10,7 @@\n"
        " old\n"
        "+added\n"
    )

    @patch("server.gh_run")
    def test_success(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout=self.DIFF_OUTPUT, stderr="")
        resp = client.get("/api/pr-diff?repo=example/repo&number=100")
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert "README.md" in data["files"]
        assert "src/main.py" in data["files"]
        assert "+new line" in data["files"]["README.md"]

    @patch("server.gh_run")
    def test_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        resp = client.get("/api/pr-diff?repo=example/repo&number=9999")
        assert resp.status_code == 500

    @patch("server.gh_run")
    def test_empty_diff(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.get("/api/pr-diff?repo=example/repo&number=100")
        assert resp.status_code == 200
        assert resp.json()["files"] == {}

    @patch("server.gh_run")
    def test_single_file_diff(self, mock_gh, client, patched_server):
        diff = "diff --git a/only.txt b/only.txt\n+hello\n"
        mock_gh.return_value = MagicMock(returncode=0, stdout=diff, stderr="")
        resp = client.get("/api/pr-diff?repo=example/repo&number=100")
        assert resp.status_code == 200
        files = resp.json()["files"]
        assert "only.txt" in files


# ---------------------------------------------------------------------------
# POST /api/pr-title
# ---------------------------------------------------------------------------

class TestPrTitle:
    @patch("server.gh_run")
    def test_success(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-title", json={
            "repo": "example/repo", "number": 100, "title": "New Title"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify DB was updated (PR 100 exists in seed data)
        pr = patched_server._db.find_pr_by_number(100)
        assert pr is not None
        assert pr["title"] == "New Title"

    @patch("server.gh_run")
    def test_empty_title(self, mock_gh, client, patched_server):
        resp = client.post("/api/pr-title", json={
            "repo": "example/repo", "number": 100, "title": "   "
        })
        assert resp.status_code == 422

    @patch("server.gh_run")
    def test_gh_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="auth error")
        resp = client.post("/api/pr-title", json={
            "repo": "example/repo", "number": 100, "title": "Title"
        })
        assert resp.status_code == 500

    @patch("server.gh_run")
    def test_untracked_pr_title(self, mock_gh, client, patched_server):
        """Updating title for a PR not in the DB still succeeds (gh edit works)."""
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-title", json={
            "repo": "example/repo", "number": 99999, "title": "Untracked PR"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True


# ---------------------------------------------------------------------------
# POST /api/pr-comment-reply
# ---------------------------------------------------------------------------

class TestPrCommentReply:
    @patch("server.gh_run")
    def test_issue_comment_reply(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment-reply", json={
            "repo": "example/repo", "number": 100, "comment_id": 42,
            "body": "Thanks for the review!", "is_review_comment": False,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify the correct API endpoint was used
        call_args = mock_gh.call_args[0][0]
        assert "issues" in " ".join(call_args)

    @patch("server.gh_run")
    def test_review_comment_reply(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment-reply", json={
            "repo": "example/repo", "number": 100, "comment_id": 42,
            "body": "Fixed in latest commit", "is_review_comment": True,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Verify the correct API endpoint was used (pulls/comments)
        call_args = mock_gh.call_args[0][0]
        assert "pulls" in " ".join(call_args)

    @patch("server.gh_run")
    def test_reply_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
        resp = client.post("/api/pr-comment-reply", json={
            "repo": "example/repo", "number": 100, "comment_id": 42,
            "body": "reply", "is_review_comment": False,
        })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/pr-comment-edit
# ---------------------------------------------------------------------------

class TestPrCommentEdit:
    @patch("server.gh_run")
    def test_edit_issue_comment(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment-edit", json={
            "repo": "example/repo", "comment_id": 42,
            "body": "Updated comment body", "is_review_comment": False,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        call_args = mock_gh.call_args[0][0]
        assert "issues/comments" in " ".join(call_args)
        assert "PATCH" in call_args

    @patch("server.gh_run")
    def test_edit_review_comment(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment-edit", json={
            "repo": "example/repo", "comment_id": 42,
            "body": "Updated review comment", "is_review_comment": True,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        call_args = mock_gh.call_args[0][0]
        assert "pulls/comments" in " ".join(call_args)

    @patch("server.gh_run")
    def test_edit_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="forbidden")
        resp = client.post("/api/pr-comment-edit", json={
            "repo": "example/repo", "comment_id": 42,
            "body": "edit", "is_review_comment": False,
        })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/pr-thread-resolve
# ---------------------------------------------------------------------------

class TestPrThreadResolve:
    @patch("server.gh_run")
    def test_resolve(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-thread-resolve", json={
            "thread_id": "THREAD_123", "resolve": True,
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        call_args = mock_gh.call_args[0][0]
        # Verify GraphQL mutation name
        query_str = " ".join(call_args)
        assert "resolveReviewThread" in query_str

    @patch("server.gh_run")
    def test_unresolve(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-thread-resolve", json={
            "thread_id": "THREAD_123", "resolve": False,
        })
        assert resp.status_code == 200
        call_args = mock_gh.call_args[0][0]
        query_str = " ".join(call_args)
        assert "unresolveReviewThread" in query_str

    @patch("server.gh_run")
    def test_resolve_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="graphql error")
        resp = client.post("/api/pr-thread-resolve", json={
            "thread_id": "THREAD_123", "resolve": True,
        })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/pr-lookup/{number}
# ---------------------------------------------------------------------------

class TestPrLookup:
    def test_found(self, client, patched_server):
        resp = client.get("/api/pr-lookup/100")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is True
        assert data["number"] == 100
        assert data["project"] == "test-proj"
        assert data["task_id"] == "task-a"

    def test_found_returns_url_for_full_repo_resolution(
        self, client, patched_server,
    ):
        """Frontend uses the URL field to recover the full `owner/repo`
        when an old browser URL only carries a bare `pr_repo`. Without
        this field the PR detail fetch would 404 against gh."""
        resp = client.get("/api/pr-lookup/100")
        data = resp.json()
        assert data["found"] is True
        assert data["url"] == "https://github.com/example/repo/pull/100"

    def test_not_found(self, client, patched_server):
        resp = client.get("/api/pr-lookup/99999")
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] is False


# ---------------------------------------------------------------------------
# POST /api/pr-refresh/{number}
# ---------------------------------------------------------------------------

class TestPrRefresh:
    @patch("common.prs.get_pr_detail")
    def test_refresh_marks_dirty_and_syncs(self, mock_detail, client, patched_server):
        mock_detail.return_value = {
            "state": "OPEN",
            "title": "Refreshed Title",
            "statusCheckRollup": [
                {"conclusion": "SUCCESS", "status": "completed"},
            ],
            "reviewDecision": "APPROVED",
            "comments": [{"id": 1}],
            "reviews": [{"id": 2}],
            "additions": 15,
            "deletions": 3,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify DB was updated
        pr = patched_server._db.find_pr_by_number(100)
        assert pr is not None
        assert pr["title"] == "Refreshed Title"
        assert pr["additions"] == 15

    @patch("common.prs.get_pr_detail")
    def test_refresh_with_ci_failure(self, mock_detail, client, patched_server):
        mock_detail.return_value = {
            "state": "OPEN",
            "title": "PR with failing CI",
            "statusCheckRollup": [
                {"conclusion": "FAILURE", "status": "completed"},
                {"conclusion": "SUCCESS", "status": "completed"},
            ],
            "reviewDecision": "",
            "comments": [],
            "reviews": [],
            "additions": 5,
            "deletions": 1,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["ci_status"] == "failure"

    @patch("common.prs.get_pr_detail")
    def test_refresh_changes_requested(self, mock_detail, client, patched_server):
        mock_detail.return_value = {
            "state": "OPEN",
            "title": "Changes needed",
            "statusCheckRollup": [],
            "reviewDecision": "CHANGES_REQUESTED",
            "comments": [],
            "reviews": [{"id": 1}],
            "additions": 2,
            "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["review_status"] == "changes_requested"

    def test_refresh_untracked_pr(self, client, patched_server):
        """Refreshing a PR that does not exist in the DB still returns ok."""
        resp = client.post("/api/pr-refresh/99999")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_refresh_pr_with_empty_url_clears_dirty(
        self, client, patched_server,
    ):
        """A PR row with an empty URL (audit case: rows where the URL
        column was lost during a faulty migration) used to leave the
        dirty flag set forever -- the next sync tick would retry, hit
        the same empty URL, and re-fail. The route now clears the
        dirty flag on the early-return so the bad row stops blocking
        the sync queue."""
        # Mark PR 100 dirty + clear its URL so repo_from_pr_url returns "".
        patched_server._db.update_pr_by_number(100, url="")
        patched_server._db._conn.execute(
            "UPDATE prs SET dirty=1 WHERE number=?", (100,),
        )
        patched_server._db._conn.commit()
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"ok": True, "changed": False, "fetched": False}
        # Dirty flag must be cleared so the queue moves on.
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["dirty"] == 0

    @patch("common.prs.get_pr_detail")
    def test_refresh_pr_when_get_pr_detail_returns_none(
        self, mock_detail, client, patched_server,
    ):
        """gh CLI failure -> get_pr_detail returns None. The route must
        clear the dirty flag (so the queue isn't stuck retrying) and
        return the no-op shape, NOT crash on `detail["state"]`."""
        mock_detail.return_value = None
        # Mark dirty so we can verify the clear path.
        patched_server._db._conn.execute(
            "UPDATE prs SET dirty=1 WHERE number=?", (100,),
        )
        patched_server._db._conn.commit()
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"ok": True, "changed": False, "fetched": False}
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["dirty"] == 0

    @patch("common.prs.get_pr_detail")
    def test_refresh_backfills_empty_author(
        self, mock_detail, client, patched_server,
    ):
        """Real incident (audit found): a PR row with `author=""` was
        invisible in workstats. Refresh now backfills the author from
        GitHub's `author.login` so the row counts toward merged PRs."""
        # Seed PR 100 with empty author.
        patched_server._db.update_pr_by_number(100, author="")
        mock_detail.return_value = {
            "state": "OPEN",
            "title": "Whatever",
            "author": {"login": "test-author"},
            "statusCheckRollup": [],
            "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["author"] == "test-author"

    @patch("common.prs.get_pr_detail")
    def test_refresh_skips_author_when_github_omits_it(
        self, mock_detail, client, patched_server,
    ):
        """Defensive: if GitHub returns no author (e.g. ghost user) we
        must NOT clobber a previously-correct author with empty."""
        patched_server._db.update_pr_by_number(100, author="prior-author")
        mock_detail.return_value = {
            "state": "OPEN", "title": "x",
            "author": None,  # missing
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["author"] == "prior-author"

    @patch("common.prs.get_pr_detail")
    def test_refresh_emits_event_when_changed(self, mock_detail, client, patched_server):
        """Real changes (e.g. CI status flip) push a github.pr.updated
        event so the frontend re-fetches instead of holding stale data."""
        # Seed PR 100 with ci_status='' so a SUCCESS rollup is a real change.
        patched_server._db.update_pr_by_number(100, ci_status="", review_status="")
        mock_detail.return_value = {
            "state": "OPEN", "title": "Changed Title",
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "completed"}],
            "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 1, "deletions": 1,
        }
        emitted = []
        with patch("app_state.emit_event",
                   side_effect=lambda t, d, **k: emitted.append((t, d, k))):
            resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        assert resp.json()["changed"] is True
        # github.pr.updated was emitted with the right pr_number; it's
        # push-only (persist=False) since this is a silent state sync.
        refresh_events = [e for e in emitted if e[0] == "github.pr.updated"]
        assert len(refresh_events) == 1
        ev_type, payload, kwargs = refresh_events[0]
        assert payload["pr_number"] == 100
        assert kwargs.get("persist") is False

    @patch("common.prs.get_pr_detail")
    def test_refresh_no_event_when_unchanged(self, mock_detail, client, patched_server):
        """No-op refresh (DB already current) shouldn't fire a noisy event."""
        # Seed exactly the values we'll return so update_pr_by_number is a no-op.
        patched_server._db.update_pr_by_number(
            100, status="open", title="T", ci_status="success",
            review_status="approved", comment_count=0, additions=0, deletions=0,
        )
        mock_detail.return_value = {
            "state": "OPEN", "title": "T",
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "completed"}],
            "reviewDecision": "APPROVED",
            "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
        }
        emitted = []
        with patch("app_state.emit_event",
                   side_effect=lambda t, d, **k: emitted.append((t, d, k))):
            resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        assert resp.json()["changed"] is False
        assert [e for e in emitted if e[0] == "github.pr.updated"] == []

    @patch("common.prs.get_pr_detail")
    def test_refresh_writes_github_updatedAt_not_local_now(
            self, mock_detail, client, patched_server):
        """`last_updated` must track GitHub's own `updatedAt`, NOT Eva's
        wall-clock at refresh time. Previously we bumped it to now() so
        the PRCard would say "just now", which was misleading for PRs
        that actually had no activity for weeks."""
        patched_server._db.update_pr_by_number(
            100, ci_status="success", last_updated="2020-01-01T00:00:00Z",
        )
        # GitHub replies "PR last touched on Jan 3, 2020".
        mock_detail.return_value = {
            "state": "OPEN", "title": "x",
            "updatedAt": "2020-01-03T12:00:00Z",
            "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "completed"}],
            "reviewDecision": "", "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        assert resp.json()["fetched"] is True
        pr = patched_server._db.find_pr_by_number(100)
        # Last-updated should be GitHub's value, NOT a fresh now() stamp.
        assert pr["last_updated"] == "2020-01-03T12:00:00Z"

    @patch("common.prs.get_pr_detail")
    def test_refresh_keeps_previous_last_updated_when_github_omits_it(
            self, mock_detail, client, patched_server):
        """Some closed/archived PR responses don't include updatedAt. Rather
        than blanking the field or writing a fresh now-stamp, keep the
        value we had before."""
        patched_server._db.update_pr_by_number(
            100, ci_status="success", last_updated="2025-06-15T09:00:00Z",
        )
        mock_detail.return_value = {
            "state": "OPEN", "title": "x",
            # no updatedAt
            "statusCheckRollup": [],
            "reviewDecision": "", "comments": [], "reviews": [],
            "additions": 0, "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["last_updated"] == "2025-06-15T09:00:00Z"

    @patch("common.prs.get_pr_detail")
    def test_refresh_backfills_status_changed_at_from_mergedAt(
            self, mock_detail, client, patched_server):
        """Legacy rows that predate the `status_changed_at` column have
        that field empty. On the next refresh we should backfill it from
        GitHub's own mergedAt / closedAt so the worklog picks up real
        transitions instead of quietly skipping the row."""
        # Seed a merged PR with no status_changed_at (simulates legacy).
        patched_server._db.update_pr_by_number(
            100, status="merged", status_changed_at="",
        )
        mock_detail.return_value = {
            "state": "MERGED", "title": "x",
            "updatedAt": "2026-04-20T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",   # earlier than updatedAt
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [], "additions": 0, "deletions": 0,
        }
        resp = client.post("/api/pr-refresh/100")
        assert resp.status_code == 200
        pr = patched_server._db.find_pr_by_number(100)
        # Backfilled from mergedAt, NOT from Eva's local clock.
        assert pr["status_changed_at"] == "2026-04-18T03:00:00Z"

    @patch("common.prs.get_pr_detail")
    def test_refresh_preserves_existing_status_changed_at(
            self, mock_detail, client, patched_server):
        """Once set, `status_changed_at` should not shift around on
        subsequent polls. The worklog relies on a stable transition time."""
        patched_server._db.update_pr_by_number(
            100, status="merged", status_changed_at="2026-01-01T00:00:00Z",
        )
        mock_detail.return_value = {
            "state": "MERGED", "title": "x",
            "updatedAt": "2026-04-22T10:00:00Z",
            "mergedAt": "2026-04-18T03:00:00Z",   # different value
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [], "additions": 0, "deletions": 0,
        }
        client.post("/api/pr-refresh/100")
        pr = patched_server._db.find_pr_by_number(100)
        # Earlier stamp wins -- we never overwrite a non-empty value.
        assert pr["status_changed_at"] == "2026-01-01T00:00:00Z"

    @patch("common.prs.get_pr_detail")
    def test_refresh_uses_closedAt_when_not_merged(
            self, mock_detail, client, patched_server):
        """Non-merged closed PRs get their timestamp from `closedAt`
        since `mergedAt` is null."""
        patched_server._db.update_pr_by_number(
            100, status="closed", status_changed_at="",
        )
        mock_detail.return_value = {
            "state": "CLOSED", "title": "x",
            "updatedAt": "2026-04-20T00:00:00Z",
            "mergedAt": None,
            "closedAt": "2026-04-18T12:00:00Z",
            "statusCheckRollup": [], "reviewDecision": "",
            "comments": [], "reviews": [], "additions": 0, "deletions": 0,
        }
        client.post("/api/pr-refresh/100")
        pr = patched_server._db.find_pr_by_number(100)
        assert pr["status_changed_at"] == "2026-04-18T12:00:00Z"


# ---------------------------------------------------------------------------
# GET /api/all-prs
# ---------------------------------------------------------------------------

class TestAllPrs:
    def test_no_filters(self, client, patched_server):
        resp = client.get("/api/all-prs")
        assert resp.status_code == 200
        # Returns grouped by project; verify structure
        data = resp.json()
        assert isinstance(data, (dict, list))

    def test_with_status_filter(self, client, patched_server):
        resp = client.get("/api/all-prs?status=open")
        assert resp.status_code == 200

    def test_with_search_filter(self, client, patched_server):
        resp = client.get("/api/all-prs?search=Task")
        assert resp.status_code == 200

    def test_with_both_filters(self, client, patched_server):
        resp = client.get("/api/all-prs?status=merged&search=repo")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/review-requests
# ---------------------------------------------------------------------------

class TestMyReviewState:
    """Unit tests for the GitHub -> my_review_state pill logic."""

    def test_pending_when_in_review_requests(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [{"login": "test-author"}, {"login": "other"}],
            "latestReviews": [
                {"author": {"login": "test-author"}, "state": "APPROVED"},
            ],
        }
        # Pending wins -- someone re-requested me after my approval.
        assert _compute_my_review_state(detail, {"test-author"}) == "pending_review"

    def test_approved_maps_to_approved(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [],
            "latestReviews": [{"author": {"login": "me"}, "state": "APPROVED"}],
        }
        assert _compute_my_review_state(detail, {"me"}) == "approved"

    def test_changes_requested_maps(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [],
            "latestReviews": [{"author": {"login": "me"}, "state": "CHANGES_REQUESTED"}],
        }
        assert _compute_my_review_state(detail, {"me"}) == "changes_requested"

    def test_commented_maps(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [],
            "latestReviews": [{"author": {"login": "me"}, "state": "COMMENTED"}],
        }
        assert _compute_my_review_state(detail, {"me"}) == "commented"

    def test_dismissed_is_treated_as_empty(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [],
            "latestReviews": [{"author": {"login": "me"}, "state": "DISMISSED"}],
        }
        assert _compute_my_review_state(detail, {"me"}) == ""

    def test_not_involved_returns_empty(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [{"login": "someone"}],
            "latestReviews": [{"author": {"login": "someone"}, "state": "APPROVED"}],
        }
        assert _compute_my_review_state(detail, {"me"}) == ""

    def test_case_insensitive_login_match(self):
        from common.prs import _compute_my_review_state
        detail = {
            "reviewRequests": [{"login": "TEST-Author"}],
            "latestReviews": [],
        }
        assert _compute_my_review_state(detail, {"test-author"}) == "pending_review"

    def test_db_enum_rejects_bad_value(self, patched_server):
        import pytest
        with pytest.raises(ValueError, match="my_review_state"):
            patched_server._db.upsert_review_pr(
                url="https://github.com/x/y/pull/1",
                repo="x/y", number=1, my_review_state="not_a_real_state",
            )


class TestPrReviewSubmit:
    """POST /api/pr-review submits an Approve / RequestChanges / Comment
    review through `gh api`, matching GitHub's Review-changes dialog."""

    @patch("app_state.gh_run_or_raise")
    def test_approve_does_not_require_body(self, mock_raise, client):
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "APPROVE", "body": "",
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["event"] == "APPROVE"
        # Shelled out with the right URL + event, no body flag.
        args, kwargs = mock_raise.call_args
        cmd = args[0]
        assert "repos/example/repo/pulls/42/reviews" in cmd
        assert "event=APPROVE" in cmd
        # No body flag pair present.
        assert not any(c.startswith("body=") for c in cmd)

    @patch("app_state.gh_run_or_raise")
    def test_request_changes_requires_body(self, mock_raise, client):
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "REQUEST_CHANGES", "body": "",
        })
        assert resp.status_code == 422
        mock_raise.assert_not_called()

    @patch("app_state.gh_run_or_raise")
    def test_comment_requires_body(self, mock_raise, client):
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "COMMENT", "body": "   ",   # whitespace -> empty
        })
        assert resp.status_code == 422
        mock_raise.assert_not_called()

    @patch("app_state.gh_run_or_raise")
    def test_request_changes_with_body_passes_through(self, mock_raise, client):
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "REQUEST_CHANGES", "body": "please fix the typo",
        })
        assert resp.status_code == 200
        cmd = mock_raise.call_args[0][0]
        assert "event=REQUEST_CHANGES" in cmd
        assert "body=please fix the typo" in cmd

    def test_unknown_event_rejected(self, client):
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "WHATEVER", "body": "hi",
        })
        assert resp.status_code == 422

    @patch("app_state.gh_run_or_raise")
    def test_submit_marks_related_rows_dirty(self, mock_raise, client, patched_server):
        """After submitting, the PR's row in `prs` AND in `review_prs`
        should be flagged for the next sync so the UI reflects the new
        review_status quickly."""
        # Seed a review_prs row so mark_review_pr_dirty has something to hit.
        patched_server._db.upsert_review_pr(
            url="https://github.com/example/repo/pull/42",
            repo="example/repo", number=42, title="t", author="other",
            source="github",
        )
        resp = client.post("/api/pr-review", json={
            "repo": "example/repo", "number": 42,
            "event": "APPROVE", "body": "",
        })
        assert resp.status_code == 200
        dirty = patched_server._db.list_dirty_review_prs()
        assert len(dirty) == 1
        assert dirty[0]["number"] == 42


def _sync_reviews():
    """Helper: drive the GitHub sync synchronously so tests can then
    assert on the resulting table state. Production triggers the same
    core function via the scheduler + `/api/review-requests/sync`;
    making it direct here keeps existing assertions unchanged after
    the list endpoint went DB-only."""
    from common.prs import sync_review_requests as _sr
    return _sr()


class TestReviewRequests:
    """/api/review-requests + sync_review_requests flow."""

    @patch("app_state.gh_run_json")
    def test_merges_two_accounts_and_dedups(self, mock_json, client, patched_server):
        from common.prs import sync_review_requests as _sync
        # Account-per-hint: example/repo -> OSS token, myorg/... -> db.
        # Same PR visible on both tokens must appear ONCE in the output.
        # The route makes TWO gh calls per account (review-requested +
        # mentions), so we only return review-requested data here -- the
        # mention query returns [] since this test focuses on the
        # review-requested dedup logic.
        oss = [{
            "number": 55222, "title": "[EX-55608] Refactor",
            "url": "https://github.com/example/repo/pull/55222",
            "state": "OPEN", "author": {"login": "someone"},
            "updatedAt": "2026-04-22T10:00:00Z",
            "repository": {"nameWithOwner": "example/repo"},
        }]
        db = [
            {
                "number": 212484, "title": "[ALT-1] runtime thing",
                "url": "https://github.com/myorg/svc/pull/212484",
                "state": "OPEN", "author": {"login": "someone"},
                "updatedAt": "2026-04-22T12:00:00Z",
                "repository": {"nameWithOwner": "myorg/svc"},
            },
            {  # duplicate of the OSS entry (hypothetically reachable from both)
                "number": 55222, "title": "[EX-55608] Refactor",
                "url": "https://github.com/example/repo/pull/55222",
                "state": "OPEN", "author": {"login": "someone"},
                "updatedAt": "2026-04-22T10:00:00Z",
                "repository": {"nameWithOwner": "example/repo"},
            },
        ]
        review_calls = {"n": 0}
        def fake(cmd, repo="", timeout=20, default=None):
            if "--review-requested=@me" in cmd:
                review_calls["n"] += 1
                return oss if "example/repo" in repo else db
            # mention sync: empty so dedup assertions stay focused.
            return []
        mock_json.side_effect = fake

        _sync()
        resp = client.get("/api/review-requests")
        assert resp.status_code == 200
        prs = resp.json()["prs"]
        # review-requested hit both accounts.
        assert review_calls["n"] == 2
        # Deduped to 2 PRs (not 3).
        assert len(prs) == 2
        numbers = sorted(p["number"] for p in prs)
        assert numbers == [55222, 212484]
        # Sorted by updated desc.
        assert prs[0]["number"] == 212484

    @patch("app_state.gh_run_json")
    def test_handles_empty_results_gracefully(self, mock_json, client):
        mock_json.return_value = []
        _sync_reviews()
        resp = client.get("/api/review-requests")
        assert resp.status_code == 200
        assert resp.json()["prs"] == []

    @patch("app_state.gh_run_json")
    def test_fills_defaults_for_missing_pr_fields(self, mock_json, client):
        """PRCard expects ci_status / review_status / comment_count etc;
        `gh search prs` doesn't return those. The core fills defaults so
        the frontend doesn't need to babysit undefined keys."""
        # Search returns a list; `gh pr view` enrichment returns {} so
        # defaults stick.
        def fake(cmd, repo="", timeout=20, default=None):
            if "search" in cmd:
                return [{
                    "number": 1, "title": "x",
                    "url": "https://github.com/a/b/pull/1",
                    "state": "OPEN", "author": {"login": "u"},
                    "updatedAt": "2026-04-22T00:00:00Z",
                    "repository": {"nameWithOwner": "a/b"},
                }]
            return {}  # `gh pr view`
        mock_json.side_effect = fake
        _sync_reviews()
        resp = client.get("/api/review-requests")
        pr = resp.json()["prs"][0]
        for key in ("ci_status", "review_status", "comment_count",
                    "additions", "deletions", "head_branch", "base_branch"):
            assert key in pr, f"expected {key} in response"

    @patch("app_state.gh_run_json")
    def test_enriches_ci_and_review_from_gh_pr_view(self, mock_json, client):
        """Each PR in the queue gets a `gh pr view` follow-up call that
        fills in CI / review / diff / branch data -- PRCard was showing
        fake 'unknown' CI rings before."""
        view_calls = []
        def fake(cmd, repo="", timeout=20, default=None):
            if "search" in cmd and "example/repo" in repo:
                return [{
                    "number": 42, "title": "real PR",
                    "url": "https://github.com/example/repo/pull/42",
                    "state": "OPEN", "author": {"login": "colleague"},
                    "updatedAt": "2026-04-23T00:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }]
            if "search" in cmd:
                return []
            if cmd[:3] == ["gh", "pr", "view"]:
                view_calls.append(cmd)
                return {
                    "statusCheckRollup": [{"conclusion": "SUCCESS", "status": "completed"}],
                    "reviewDecision": "APPROVED",
                    "comments": [{"id": 1}, {"id": 2}],
                    "reviews": [{"id": 3}],
                    "additions": 120, "deletions": 17,
                    "headRefName": "feature-x", "baseRefName": "master",
                }
            return {}
        mock_json.side_effect = fake

        _sync_reviews()
        resp = client.get("/api/review-requests")
        prs = [p for p in resp.json()["prs"] if p["number"] == 42]
        assert len(prs) == 1
        pr = prs[0]
        assert pr["ci_status"] == "success"
        assert pr["review_status"] == "approved"
        assert pr["comment_count"] == 3  # 2 comments + 1 review
        assert pr["additions"] == 120
        assert pr["deletions"] == 17
        assert pr["head_branch"] == "feature-x"
        assert pr["base_branch"] == "master"
        # The enrichment call targeted the right repo + number.
        assert any("--repo" in c and "example/repo" in c and "42" in c for c in view_calls)


# ---------------------------------------------------------------------------
# POST/DELETE /api/review-requests/watchlist
# ---------------------------------------------------------------------------

class TestReviewWatchlist:
    """Manual pinning of PRs to the review queue."""

    @patch("app_state.gh_run_json")
    def test_add_fetches_metadata_and_persists_row(
            self, mock_json, client, patched_server):
        # One call to fetch PR metadata (title/state/author/updatedAt).
        mock_json.return_value = {
            "title": "Please review my PR",
            "state": "OPEN",
            "author": {"login": "alice"},
            "updatedAt": "2026-04-23T01:00:00Z",
        }
        resp = client.post(
            "/api/review-requests/watchlist",
            json={"url": "https://github.com/owner/repo/pull/42"},
        )
        assert resp.status_code == 201
        row = patched_server._db.get_review_watch(
            "https://github.com/owner/repo/pull/42")
        assert row is not None
        assert row["repo"] == "owner/repo"
        assert row["number"] == 42
        assert row["title"] == "Please review my PR"
        assert row["author"] == "alice"
        assert row["status"] == "open"
        assert row["added_at"]

    def test_add_rejects_non_pr_url(self, client):
        resp = client.post(
            "/api/review-requests/watchlist",
            json={"url": "https://example.com/not-a-pr"},
        )
        assert resp.status_code == 422

    @patch("app_state.gh_run_json")
    def test_add_is_idempotent_preserves_added_at(
            self, mock_json, client, patched_server):
        """Re-adding the same URL must NOT reset added_at -- the user's
        original pin time is what sorts the list."""
        mock_json.return_value = {
            "title": "t", "state": "OPEN", "author": {"login": "u"},
            "updatedAt": "2026-04-23T02:00:00Z",
        }
        url = "https://github.com/owner/repo/pull/1"
        client.post("/api/review-requests/watchlist", json={"url": url})
        first = patched_server._db.get_review_watch(url)["added_at"]
        client.post("/api/review-requests/watchlist", json={"url": url})
        second = patched_server._db.get_review_watch(url)["added_at"]
        assert first == second

    def test_delete_removes_row(self, client, patched_server):
        url = "https://github.com/owner/repo/pull/7"
        patched_server._db.add_review_watch(
            url=url, repo="owner/repo", number=7, title="t",
        )
        resp = client.delete(f"/api/review-requests/watchlist?url={url}")
        assert resp.status_code == 200
        assert patched_server._db.get_review_watch(url) is None

    def test_delete_unknown_url_returns_404(self, client):
        resp = client.delete(
            "/api/review-requests/watchlist"
            "?url=https://github.com/x/y/pull/9999"
        )
        assert resp.status_code == 404

    @patch("app_state.gh_run_json")
    def test_list_merges_watchlist_with_github_results(
            self, mock_json, client, patched_server):
        # GitHub returns one review-requested PR; watchlist has another.
        # Keep mentions empty so this test isolates the review-vs-manual
        # merge logic (a separate test covers mention-auto-adding).
        def fake(cmd, repo="", timeout=20, default=None):
            if "--review-requested=@me" in cmd:
                return [{
                    "number": 100, "title": "gh one",
                    "url": "https://github.com/example/repo/pull/100",
                    "state": "OPEN", "author": {"login": "u"},
                    "updatedAt": "2026-04-22T00:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }] if "example/repo" in repo else []
            if "--mentions=@me" in cmd:
                return []
            # pr view call from manual-add
            return {"title": "manual one", "state": "OPEN",
                    "author": {"login": "bob"},
                    "updatedAt": "2026-04-22T12:00:00Z"}
        mock_json.side_effect = fake

        patched_server._db.add_review_watch(
            url="https://github.com/owner/repo/pull/55",
            repo="owner/repo", number=55, title="manual one",
            author="bob", last_updated="2026-04-22T12:00:00Z",
        )
        _sync_reviews()
        resp = client.get("/api/review-requests")
        prs = resp.json()["prs"]
        # Both present, each tagged by source.
        by_num = {p["number"]: p for p in prs}
        assert by_num[100]["source"] == "github"
        assert by_num[55]["source"] == "manual"

    @patch("app_state.gh_run_json")
    def test_list_marks_overlapping_pr_as_both(
            self, mock_json, client, patched_server):
        """PR that is BOTH GitHub-tagged AND manually pinned -> source='both'
        so the UI still shows an Unpin button."""
        def fake(cmd, repo="", timeout=20, default=None):
            if "search" in cmd:
                return [{
                    "number": 77, "title": "t",
                    "url": "https://github.com/example/repo/pull/77",
                    "state": "OPEN", "author": {"login": "u"},
                    "updatedAt": "2026-04-22T00:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }] if ("example/repo" in repo and "--review-requested=@me" in cmd) else []
            return {}
        mock_json.side_effect = fake

        patched_server._db.add_review_watch(
            url="https://github.com/example/repo/pull/77",
            repo="example/repo", number=77, title="t",
        )
        _sync_reviews()
        resp = client.get("/api/review-requests")
        prs = [p for p in resp.json()["prs"] if p["number"] == 77]
        assert len(prs) == 1
        assert prs[0]["source"] == "both"

    @patch("app_state.gh_run_json")
    def test_at_mentions_auto_added_to_watchlist(
            self, mock_json, client, patched_server):
        """A PR that @mentions me (even without a formal review request)
        must land in the review queue automatically. Sync happens on
        each /api/review-requests hit via sync_mention_watchlist."""
        def fake(cmd, repo="", timeout=20, default=None):
            if "--mentions=@me" in cmd and "example/repo" in repo:
                return [{
                    "number": 321, "title": "ping",
                    "url": "https://github.com/example/repo/pull/321",
                    "state": "OPEN", "author": {"login": "carol"},
                    "updatedAt": "2026-04-23T03:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }]
            return []
        mock_json.side_effect = fake

        assert patched_server._db.list_review_watch() == []
        _sync_reviews()
        resp = client.get("/api/review-requests")
        assert resp.status_code == 200
        watch = patched_server._db.list_review_watch()
        assert len(watch) == 1
        assert watch[0]["number"] == 321
        assert watch[0]["title"] == "ping"
        assert watch[0]["author"] == "carol"
        # And it shows up in the response as a manual entry.
        mentioned = [p for p in resp.json()["prs"] if p["number"] == 321]
        assert len(mentioned) == 1
        assert mentioned[0]["source"] == "manual"

    @patch("app_state.gh_run_json")
    @patch.dict("adapters.github._gh_tokens",
                {"test-author": "t1", "test-author_data": "t2"}, clear=True)
    def test_my_own_prs_are_excluded_from_review_queue(
            self, mock_json, client, patched_server):
        """PRs I authored must never show up in "All Reviews" -- applies
        to both the review-requested feed AND the @mention sync. Author
        match is case-insensitive."""
        def fake(cmd, repo="", timeout=20, default=None):
            # Every search (both --review-requested and --mentions) returns
            # one PR authored by me + one authored by someone else.
            if "search" in cmd:
                return [
                    {
                        "number": 1, "title": "my own PR",
                        "url": "https://github.com/example/repo/pull/1",
                        "state": "OPEN",
                        "author": {"login": "TEST-AUTHOR"},  # case variant
                        "updatedAt": "2026-04-23T00:00:00Z",
                        "repository": {"nameWithOwner": "example/repo"},
                    },
                    {
                        "number": 2, "title": "colleague PR",
                        "url": "https://github.com/example/repo/pull/2",
                        "state": "OPEN", "author": {"login": "colleague"},
                        "updatedAt": "2026-04-23T00:00:00Z",
                        "repository": {"nameWithOwner": "example/repo"},
                    },
                ] if "example/repo" in repo else []
            return {}
        mock_json.side_effect = fake

        _sync_reviews()
        resp = client.get("/api/review-requests")
        assert resp.status_code == 200
        numbers = [p["number"] for p in resp.json()["prs"]]
        # My PR (#1) is filtered; colleague's (#2) is not.
        assert 1 not in numbers
        assert 2 in numbers
        # Mention sync also skipped it -- no watchlist row for #1.
        urls = [w["url"] for w in patched_server._db.list_review_watch()]
        assert "https://github.com/example/repo/pull/1" not in urls

    def test_list_is_db_only_no_gh_calls(self, client, patched_server):
        """GET /api/review-requests MUST NOT hit GitHub. It reads
        straight from `review_prs`. Any gh traffic should come from
        the sync worker, not the list endpoint."""
        patched_server._db.upsert_review_pr(
            url="https://github.com/owner/repo/pull/1",
            repo="owner/repo", number=1, title="pre-seeded",
            author="someone", source="github",
            last_updated="2026-04-23T00:00:00Z",
        )
        # Patch gh_run_json so any call raises -- list shouldn't reach
        # it at all.
        with patch("app_state.gh_run_json",
                   side_effect=AssertionError("list must not call gh")):
            resp = client.get("/api/review-requests")
        assert resp.status_code == 200
        prs = resp.json()["prs"]
        assert [p["number"] for p in prs] == [1]

    def test_sync_endpoint_returns_immediately(self, client, patched_server):
        """POST /api/review-requests/sync kicks off a background thread
        and returns right away -- it does NOT block on gh latency."""
        import time
        def slow(*a, **kw):
            time.sleep(2)  # would exceed the endpoint's patience
            return []
        with patch("app_state.gh_run_json", side_effect=slow):
            start = time.monotonic()
            resp = client.post("/api/review-requests/sync")
            elapsed = time.monotonic() - start
        assert resp.status_code == 200
        assert resp.json()["status"] == "sync started"
        # Responded well under the 2s fake delay -> truly async.
        assert elapsed < 1.0
        # The conftest's `patched_server` teardown joins the thread
        # before closing the DB; without that, this test was the
        # source of the "1 in 4 full-suite-run" segfault that took an
        # entire iter to bisect.

    def test_sync_route_registers_thread_in_module_drain_set(self, client):
        """The route MUST add the spawned thread to `_SYNC_THREADS`
        so the conftest teardown can join it before closing the DB.
        Regression for the segfault where a daemon thread mid-flight
        in `sync_review_requests -> upsert_review_pr` raced
        `temp_db.close()` and crashed the interpreter."""
        from routes.prs import _SYNC_THREADS
        # Drain anything from a prior test in this class (the conftest
        # fixture also drains, but we run inside the same fixture
        # instance here).
        from routes.prs import _await_sync_threads
        _await_sync_threads(timeout=2.0)
        assert _SYNC_THREADS == []

        with patch("app_state.gh_run_json", return_value=[]):
            resp = client.post("/api/review-requests/sync")
        assert resp.status_code == 200
        # Thread is registered (even if it has already finished by the
        # time we look -- daemon threads can outlive their registry
        # entry, which is fine because `_await_sync_threads` only
        # joins still-alive ones).
        assert len(_SYNC_THREADS) >= 1
        # Drain so this test doesn't leak its own bg thread.
        _await_sync_threads(timeout=5.0)
        assert _SYNC_THREADS == []

    def test_await_sync_threads_drains_registry_and_completes_threads(self):
        """`_await_sync_threads` is the conftest-side teardown helper.
        Contract: when it returns, (a) the registry is empty so the
        next test starts clean, (b) every thread it tracked has run
        to completion. The exact `waited` count is timing-dependent
        (a fast thread may finish before `is_alive()` is checked) so
        we don't assert on it; the important invariants are the two
        above."""
        from routes.prs import _SYNC_THREADS, _await_sync_threads
        import threading

        events = []
        gate = threading.Event()

        def _work(label):
            gate.wait(timeout=5.0)
            events.append(label)

        for label in ("a", "b"):
            t = threading.Thread(target=_work, args=(label,), daemon=True)
            _SYNC_THREADS.append(t)
            t.start()
        gate.set()

        _await_sync_threads(timeout=2.0)
        assert _SYNC_THREADS == []
        # Both threads ran to completion before we returned.
        assert sorted(events) == ["a", "b"]

    def test_await_sync_threads_is_noop_when_registry_empty(self):
        from routes.prs import _SYNC_THREADS, _await_sync_threads
        assert _SYNC_THREADS == []
        assert _await_sync_threads(timeout=0.1) == 0

    def test_mark_review_pr_dirty_by_number(self, patched_server):
        """github_poller marks queue rows dirty by PR number (that's all
        the notification payload carries). A subsequent list_dirty call
        must surface the row."""
        db = patched_server._db
        db.upsert_review_pr(
            url="https://github.com/x/y/pull/42",
            repo="x/y", number=42, title="t", author="u", source="github",
        )
        assert db.mark_review_pr_dirty(number=42) is True
        dirty = db.list_dirty_review_prs()
        assert len(dirty) == 1
        assert dirty[0]["number"] == 42

    @patch("app_state.gh_run_json")
    def test_dirty_only_sync_refreshes_flagged_row(
            self, mock_json, patched_server):
        """sync_review_requests_dirty_only only touches rows with
        dirty=1, clears the flag, and does NOT call `gh search` (that's
        the full-sync path's job)."""
        from common.prs import sync_review_requests_dirty_only
        db = patched_server._db
        db.upsert_review_pr(
            url="https://github.com/x/y/pull/7",
            repo="x/y", number=7, title="t", author="u", source="manual",
            ci_status="unknown",
        )
        db.mark_review_pr_dirty(number=7)

        # Only `gh pr view` is expected -- no `gh search` here.
        def fake(cmd, repo="", timeout=20, default=None):
            if "search" in cmd:
                raise AssertionError("dirty-only sync must not run gh search")
            return {
                "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                "reviewDecision": "APPROVED",
                "comments": [], "reviews": [],
                "additions": 5, "deletions": 1,
                "headRefName": "h", "baseRefName": "master",
            }
        mock_json.side_effect = fake

        result = sync_review_requests_dirty_only()
        assert result == {"refreshed": 1}
        row = db.get_review_pr("https://github.com/x/y/pull/7")
        assert row["dirty"] == 0
        assert row["ci_status"] == "success"
        assert row["review_status"] == "approved"

    @patch("app_state.gh_run_json")
    def test_review_prs_table_persists_after_sync(
            self, mock_json, client, patched_server):
        """`sync_review_requests` writes rows into `review_prs`; the
        `list` endpoint then reads from the table so repeated /api/
        review-requests calls don't re-fetch on every hit in practice
        (still do `gh search` for freshness, but DB is the source of
        truth for enrichment fields)."""
        def fake(cmd, repo="", timeout=20, default=None):
            if "--review-requested=@me" in cmd and "example/repo" in repo:
                return [{
                    "number": 500, "title": "persist me",
                    "url": "https://github.com/example/repo/pull/500",
                    "state": "OPEN", "author": {"login": "other"},
                    "updatedAt": "2026-04-23T00:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }]
            if "search" in cmd:
                return []
            # pr view enrichment:
            return {"statusCheckRollup": [{"conclusion": "SUCCESS"}],
                    "reviewDecision": "APPROVED",
                    "comments": [], "reviews": [],
                    "additions": 10, "deletions": 0,
                    "headRefName": "f", "baseRefName": "master"}
        mock_json.side_effect = fake

        assert patched_server._db.list_review_prs() == []
        _sync_reviews()
        resp = client.get("/api/review-requests")
        assert resp.status_code == 200

        rows = patched_server._db.list_review_prs()
        assert len(rows) == 1
        r = rows[0]
        assert r["source"] == "github"
        assert r["ci_status"] == "success"
        assert r["review_status"] == "approved"
        assert r["additions"] == 10
        assert r["added_at"] and r["synced_at"]

    @patch("app_state.gh_run_json")
    def test_stale_github_rows_are_deleted_on_next_sync(
            self, mock_json, client, patched_server):
        """PR that used to be in the review-requested list disappears
        (merged, reviewer removed) -> row must go away on the next
        sync. Manual pins survive."""
        # Seed two DB rows: one 'github', one 'manual'.
        patched_server._db.upsert_review_pr(
            url="https://github.com/example/repo/pull/101",
            repo="example/repo", number=101, title="stale gh",
            author="other", source="github", last_updated="2026-04-20T00:00:00Z",
        )
        patched_server._db.upsert_review_pr(
            url="https://github.com/example/repo/pull/102",
            repo="example/repo", number=102, title="kept manual",
            author="other", source="manual", last_updated="2026-04-20T00:00:00Z",
        )

        # Sync returns empty -> gh row should be deleted, manual kept.
        mock_json.return_value = []
        _sync_reviews()

        urls = [r["url"] for r in patched_server._db.list_review_prs()]
        assert "https://github.com/example/repo/pull/101" not in urls
        assert "https://github.com/example/repo/pull/102" in urls

    @patch("app_state.gh_run_json")
    def test_mention_sync_is_idempotent_over_repeated_polls(
            self, mock_json, client, patched_server):
        """Re-polling a standing mention must NOT reset added_at (the
        user's "when did this first show up" anchor) and must NOT grow
        duplicate rows."""
        def fake(cmd, repo="", timeout=20, default=None):
            if "--mentions=@me" in cmd and "example/repo" in repo:
                return [{
                    "number": 55, "title": "standing mention",
                    "url": "https://github.com/example/repo/pull/55",
                    "state": "OPEN", "author": {"login": "dave"},
                    "updatedAt": "2026-04-23T04:00:00Z",
                    "repository": {"nameWithOwner": "example/repo"},
                }]
            return []
        mock_json.side_effect = fake

        _sync_reviews()
        first = patched_server._db.get_review_watch(
            "https://github.com/example/repo/pull/55")["added_at"]
        # Second poll.
        _sync_reviews()
        second = patched_server._db.get_review_watch(
            "https://github.com/example/repo/pull/55")["added_at"]
        assert first == second
        assert len(patched_server._db.list_review_watch()) == 1


# ---------------------------------------------------------------------------
# POST /api/all-prs/sync
# ---------------------------------------------------------------------------

class TestAllPrsSyncPost:
    @patch("server.gh_run")
    def test_sync_no_discoveries(self, mock_gh, client, patched_server):
        """Sync with no new discoveries just updates existing PRs."""
        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = json.dumps([])
            else:
                m.stdout = json.dumps({
                    "title": "T", "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01",
                    "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [], "reviewDecision": "",
                })
            return m

        mock_gh.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["discovered"] == 0
        assert data["updated"] >= 0

    @patch("server.gh_run")
    def test_sync_owner_search(self, mock_gh, client, patched_server):
        """Test sync with owner: prefix search."""
        # The actual search args are built by _build_repo_authors which
        # is called inside sync_all_prs, but we mock gh_run at the lowest level
        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = json.dumps([])
            else:
                m.stdout = json.dumps({
                    "title": "T", "state": "OPEN",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-01-01",
                    "additions": 0, "deletions": 0,
                    "comments": [], "reviews": [],
                    "headRefName": "b", "baseRefName": "master",
                    "author": {"login": "u"},
                    "statusCheckRollup": [], "reviewDecision": "",
                })
            return m

        mock_gh.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert "discovered" in data
        assert "updated" in data
        assert "total" in data
        assert "errors" in data


# ---------------------------------------------------------------------------
# POST /api/pr-comment (basic, for completeness)
# ---------------------------------------------------------------------------

class TestPrComment:
    @patch("server.gh_run")
    def test_post_comment_success(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-comment", json={
            "repo": "example/repo", "number": 100, "body": "LGTM"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("server.gh_run")
    def test_post_comment_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="forbidden")
        resp = client.post("/api/pr-comment", json={
            "repo": "example/repo", "number": 100, "body": "LGTM"
        })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/pr-body (basic, for completeness)
# ---------------------------------------------------------------------------

class TestPrBody:
    @patch("server.gh_run")
    def test_update_body_success(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=0, stdout="", stderr="")
        resp = client.post("/api/pr-body", json={
            "repo": "example/repo", "number": 100, "body": "Updated description"
        })
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    @patch("server.gh_run")
    def test_update_body_failure(self, mock_gh, client, patched_server):
        mock_gh.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        resp = client.post("/api/pr-body", json={
            "repo": "example/repo", "number": 100, "body": "new body"
        })
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# _discover_new_prs (unit tests)
# ---------------------------------------------------------------------------

class TestDiscoverNewPrs:
    """Test the _discover_new_prs function directly."""

    @patch("app_state.gh_run_json")
    @patch("app_state._db")
    def test_discover_with_repo_prefix(self, mock_db, mock_gh_json, patched_server):
        """Search with a standard repo like 'example/repo'."""
        from routes.prs import _discover_new_prs

        mock_db.find_pr_by_number.return_value = None
        mock_gh_json.return_value = [
            {"number": 501, "title": "feat: new thing", "url": "https://github.com/example/repo/pull/501",
             "state": "open", "repository": {"nameWithOwner": "example/repo"}},
        ]

        # _match_pr_to_task needs the real task_db; mock add_pr on the real db
        # The discover helper moved to prs; patch it there (the
        # routes/prs re-export is just an alias, so patching it has no
        # effect on the internals of ingest_discovered_item).
        with patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-a")), \
             patch("common.prs._resolve_pr_status", return_value="open"):
            patched_server._db.find_pr_by_number = MagicMock(return_value=None)
            disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="10")

        assert disc > 0
        assert any(p["number"] == 501 for p in prs)
        assert len(errors) == 0

    @patch("app_state.gh_run_json")
    def test_discover_with_owner_prefix(self, mock_gh_json, patched_server):
        """Search with an owner: prefix like 'owner:myorg'."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = [
            {"number": 601, "title": "fix: bug", "url": "https://github.com/myorg/monorepo/pull/601",
             "state": "open", "repository": {"nameWithOwner": "myorg/monorepo"}},
        ]

        with patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-b")), \
             patch("common.prs._resolve_pr_status", return_value="open"):
            patched_server._db.find_pr_by_number = MagicMock(return_value=None)
            disc, prs, errors = _discover_new_prs(
                {"owner:myorg": "test-author_data"}, limit="5"
            )

        assert disc > 0
        assert any(p["number"] == 601 for p in prs)

    @patch("app_state.gh_run_json")
    def test_discover_skips_existing_prs(self, mock_gh_json, patched_server):
        """PRs already tracked in the DB should be skipped."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = [
            {"number": 100, "title": "existing", "url": "https://github.com/example/repo/pull/100",
             "state": "open", "repository": {"nameWithOwner": "example/repo"}},
        ]

        # PR 100 already exists in the DB (from conftest seed)
        disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")
        assert disc == 0
        assert len(prs) == 0

    @patch("app_state.gh_run_json")
    def test_discover_no_matches(self, mock_gh_json, patched_server):
        """When gh search returns empty results."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = []
        disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")
        assert disc == 0
        assert len(prs) == 0
        assert len(errors) == 0

    @patch("app_state.gh_run_json")
    def test_discover_returns_none_from_gh(self, mock_gh_json, patched_server):
        """When gh_run_json returns None (command failure)."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = None
        disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")
        assert disc == 0
        assert len(prs) == 0

    @patch("app_state.gh_run_json")
    def test_discover_exception_caught(self, mock_gh_json, patched_server):
        """Exceptions during search are caught and added to errors list."""
        from routes.prs import _discover_new_prs

        mock_gh_json.side_effect = RuntimeError("network timeout")
        disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")
        assert disc == 0
        assert len(errors) > 0
        assert "network timeout" in errors[0]

    @patch("app_state.gh_run_json")
    def test_discover_skips_disallowed_repos(self, mock_gh_json, patched_server):
        """PRs from repos not in ALLOWED_REPOS are skipped."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = [
            {"number": 701, "title": "not tracked", "url": "https://github.com/random/repo/pull/701",
             "state": "open", "repository": {"nameWithOwner": "random/repo"}},
        ]
        patched_server._db.find_pr_by_number = MagicMock(return_value=None)
        disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")
        assert disc == 0

    @patch("app_state.gh_run_json")
    def test_discover_repo_info_as_string(self, mock_gh_json, patched_server):
        """Handle repository field as a plain string (instead of dict)."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = [
            {"number": 801, "title": "string repo", "url": "https://github.com/example/repo/pull/801",
             "state": "open", "repository": "example/repo"},
        ]

        # The discover helper moved to prs; patch it there (the
        # routes/prs re-export is just an alias, so patching it has no
        # effect on the internals of ingest_discovered_item).
        with patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-a")), \
             patch("common.prs._resolve_pr_status", return_value="open"):
            patched_server._db.find_pr_by_number = MagicMock(return_value=None)
            disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")

        assert disc > 0
        assert any(p["number"] == 801 for p in prs)

    @patch("app_state.gh_run_json")
    def test_discover_fork_resolved_to_upstream(self, mock_gh_json, patched_server):
        """Fork repos should be resolved to upstream before tracking."""
        from routes.prs import _discover_new_prs

        mock_gh_json.return_value = [
            {"number": 901, "title": "fork pr", "url": "https://github.com/test-author/repo/pull/901",
             "state": "open", "repository": {"nameWithOwner": "test-author/repo"}},
        ]

        # The discover helper moved to prs; patch it there (the
        # routes/prs re-export is just an alias, so patching it has no
        # effect on the internals of ingest_discovered_item).
        with patch("common.prs._match_pr_to_task", return_value=("test-proj", "task-a")), \
             patch("common.prs._resolve_pr_status", return_value="open"):
            patched_server._db.find_pr_by_number = MagicMock(return_value=None)
            disc, prs, errors = _discover_new_prs({"example/repo": "test-author"}, limit="5")

        assert disc > 0
        # URL should use the upstream repo
        assert any("example/repo" in p["url"] for p in prs)


# ---------------------------------------------------------------------------
# GET /api/all-prs/sync-stream (SSE)
# ---------------------------------------------------------------------------

class TestSyncStream:
    @patch("routes.prs._fetch_pr_detail")
    @patch("routes.prs._discover_repo_async")
    @patch("app_state._build_repo_authors")
    def test_sync_stream_basic(self, mock_authors, mock_discover, mock_fetch_detail,
                               client, patched_server):
        """SSE stream endpoint produces start, dirty, discover, update, done phases."""
        import asyncio

        mock_authors.return_value = {"example/repo": "test-author"}

        async def mock_discover_coro(*args, **kwargs):
            return [{"number": 999, "url": "https://github.com/example/repo/pull/999"}]

        async def mock_fetch_coro(*args, **kwargs):
            return True

        mock_discover.side_effect = mock_discover_coro
        mock_fetch_detail.side_effect = mock_fetch_coro

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

        body = resp.text
        # Verify all phases are present
        assert '"phase": "start"' in body or '"phase":"start"' in body
        assert '"phase": "dirty"' in body or '"phase":"dirty"' in body
        assert '"phase": "discover"' in body or '"phase":"discover"' in body
        assert '"phase": "done"' in body or '"phase":"done"' in body

    @patch("routes.prs._fetch_pr_detail")
    @patch("routes.prs._discover_repo_async")
    @patch("app_state._build_repo_authors")
    def test_sync_stream_full_mode(self, mock_authors, mock_discover, mock_fetch_detail,
                                   client, patched_server):
        """SSE stream with full=1 should pass full flag and update all PRs."""
        import asyncio

        mock_authors.return_value = {}

        async def mock_discover_coro(*args, **kwargs):
            return []

        async def mock_fetch_coro(*args, **kwargs):
            return True

        mock_discover.side_effect = mock_discover_coro
        mock_fetch_detail.side_effect = mock_fetch_coro

        resp = client.get("/api/all-prs/sync-stream?full=1")
        assert resp.status_code == 200
        body = resp.text
        assert '"full": true' in body or '"full":true' in body

    @patch("routes.prs._fetch_pr_detail")
    @patch("routes.prs._discover_repo_async")
    @patch("app_state._build_repo_authors")
    def test_sync_stream_no_discoveries(self, mock_authors, mock_discover, mock_fetch_detail,
                                        client, patched_server):
        """SSE stream with zero discoveries and no dirty PRs."""
        mock_authors.return_value = {}

        async def mock_discover_coro(*args, **kwargs):
            return []

        async def mock_fetch_coro(*args, **kwargs):
            return False

        mock_discover.side_effect = mock_discover_coro
        mock_fetch_detail.side_effect = mock_fetch_coro

        resp = client.get("/api/all-prs/sync-stream")
        assert resp.status_code == 200
        body = resp.text
        assert '"discovered": 0' in body or '"discovered":0' in body


# ---------------------------------------------------------------------------
# POST /api/all-prs/sync (more thorough)
# ---------------------------------------------------------------------------

class TestAllPrsSyncThorough:
    @patch("server.gh_run")
    def test_sync_with_discovery_and_update(self, mock_gh, client, patched_server):
        """Test sync that discovers a new PR and updates an existing one."""
        call_count = [0]

        def fake_gh(args, repo="", timeout=20):
            call_count[0] += 1
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = json.dumps([])
            elif "pr" in args and "view" in args:
                m.stdout = json.dumps({
                    "title": "Updated PR", "state": "MERGED",
                    "url": "https://github.com/example/repo/pull/100",
                    "updatedAt": "2026-04-15",
                    "additions": 10, "deletions": 5,
                    "comments": [{"id": 1}], "reviews": [{"id": 2}],
                    "headRefName": "fix-branch", "baseRefName": "master",
                    "author": {"login": "testuser"},
                    "statusCheckRollup": [{"conclusion": "SUCCESS"}],
                    "reviewDecision": "APPROVED",
                })
            else:
                m.stdout = json.dumps({})
            return m

        mock_gh.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] >= 1
        assert isinstance(data["errors"], list)

        # Verify the PR was updated in DB
        pr = patched_server._db.find_pr_by_number(100)
        assert pr is not None
        assert pr["title"] == "Updated PR"

    @patch("server.gh_run")
    def test_sync_handles_gh_view_failure(self, mock_gh, client, patched_server):
        """When gh pr view fails, the PR is skipped but sync continues."""
        def fake_gh(args, repo="", timeout=20):
            m = MagicMock()
            if "search" in args:
                m.returncode = 0
                m.stdout = json.dumps([])
            else:
                # pr view fails
                m.returncode = 1
                m.stdout = ""
                m.stderr = "not found"
            return m

        mock_gh.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] == 0

    @patch("server.gh_run")
    def test_sync_handles_pr_exception(self, mock_gh, client, patched_server):
        """When processing a PR throws an exception, it appears in errors."""
        call_idx = [0]

        def fake_gh(args, repo="", timeout=20):
            call_idx[0] += 1
            m = MagicMock()
            m.returncode = 0
            if "search" in args:
                m.stdout = json.dumps([])
            elif "pr" in args and "view" in args:
                raise ConnectionError("connection reset")
            else:
                m.stdout = json.dumps({})
            return m

        mock_gh.side_effect = fake_gh

        resp = client.post("/api/all-prs/sync")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["errors"]) > 0
        assert "connection reset" in data["errors"][0]


# ---------------------------------------------------------------------------
# /api/reviews* HTTP routes (tested via TestClient end-to-end)
# ---------------------------------------------------------------------------

class TestReviewRoutes:
    """Round-trip the /api/reviews/* HTTP surface so the route layer's
    error mapping (ValueError -> 422) is exercised end-to-end. The core
    helpers are covered separately; here we verify the HTTP boundary."""

    def _seed_review(self, db, url="https://github.com/x/y/pull/77"):
        # Valid workflow states are: active / dismissed / done / queued.
        db.upsert_review_pr(
            url=url, repo="x/y", number=77,
            title="needs review", state="open",
            my_workflow_state="active",
        )
        return url

    def test_open_review_session_422_on_unknown_url(self, client):
        # No review_prs row -> reviews raises ValueError -> 422.
        resp = client.post(
            "/api/reviews/open?url=https://github.com/no/such/pull/1",
            json={"action_id": "review-pr"},
        )
        assert resp.status_code == 422

    def test_open_review_session_success(self, client, patched_server, monkeypatch):
        # Stub the launcher so we don't actually spawn tmux/agent.
        url = self._seed_review(patched_server._db)
        from common import reviews as _r

        def fake_open(review_url, **kw):
            assert review_url == url
            return {"prompt": "go review", "session": "review-77"}
        monkeypatch.setattr(_r, "open_review_session", fake_open)

        resp = client.post(
            f"/api/reviews/open?url={url}",
            json={"action_id": "review-pr"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["prompt"] == "go review"

    def test_patch_review_updates_workflow_state(self, client, patched_server):
        # Round-trip update -> ensure my_workflow_state reflects what we
        # passed in. Covers the happy-path + the post-update fetch.
        url = self._seed_review(patched_server._db)
        resp = client.patch(
            f"/api/reviews?url={url}",
            json={"my_workflow_state": "done"},
        )
        assert resp.status_code == 200
        assert resp.json()["my_workflow_state"] == "done"

    def test_patch_review_422_on_missing_url(self, client):
        # common.reviews.update_review raises ValueError when the URL is
        # not in the watchlist; route maps to 422.
        resp = client.patch(
            "/api/reviews?url=https://github.com/no/such/pull/1",
            json={"my_workflow_state": "done"},
        )
        assert resp.status_code == 422

    def test_post_review_history_appends_entry(self, client, patched_server):
        url = self._seed_review(patched_server._db)
        resp = client.post(
            f"/api/reviews/history?url={url}",
            json={"text": "looked at this PR", "source": "manual"},
        )
        assert resp.status_code == 200
        # The DB returns at minimum a ts on success.
        body = resp.json()
        assert "ts" in body or "url" in body
        # The list endpoint should now show the entry.
        list_resp = client.get(f"/api/reviews/history?url={url}")
        assert list_resp.status_code == 200
        entries = list_resp.json()["entries"]
        assert any("looked at this PR" in (e.get("text") or "") for e in entries)

    def test_post_review_history_422_on_empty_text(self, client, patched_server):
        url = self._seed_review(patched_server._db)
        # append_review_history rejects empty text with ValueError -> 422.
        resp = client.post(
            f"/api/reviews/history?url={url}",
            json={"text": "", "source": "manual"},
        )
        assert resp.status_code == 422

    def test_list_review_history_empty_for_unknown_url(self, client):
        # No row, no entries -- and the route must NOT 404; it returns
        # an empty list. Frontend filters by URL itself; an HTTP error
        # would surface as a needless red banner.
        resp = client.get(
            "/api/reviews/history?url=https://github.com/no/such/pull/1",
        )
        assert resp.status_code == 200
        assert resp.json()["entries"] == []


class TestReviewSyncBackgroundExceptionLog:
    """The /api/review-requests/sync route fires a daemon thread; the
    runner must swallow exceptions so a flaky JIRA / network blip
    doesn't crash the worker thread silently. Exceptions are logged."""

    def test_runner_catches_and_logs_exception(self, capfd, monkeypatch):
        # Patch the function the runner calls to raise.
        from common import prs as _core_prs
        monkeypatch.setattr(
            _core_prs, "sync_review_requests",
            lambda: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
        # Call the route directly so we can capture stdout reliably
        # without TestClient's middleware buffering.
        from routes.prs import _SYNC_THREADS
        from routes.prs import sync_review_requests_route
        sync_review_requests_route()
        # Wait for the daemon thread to finish.
        for t in list(_SYNC_THREADS):
            if t.is_alive():
                t.join(timeout=2.0)
        out = capfd.readouterr().out
        assert "[review-sync] failed: kaboom" in out
