"""Tests for the runtime repo-allow-list override mechanism.

`adapters.github.ALLOWED_REPOS` / `FORK_TO_UPSTREAM` are module-level
mutable containers; `app_state._apply_repo_overrides_from_settings`
mutates them in-place at startup so the rest of the codebase can keep
reading the constants directly. We verify that:

  1. With settings empty, the hardcoded defaults stay intact.
  2. With settings populated, the overrides replace the hardcoded set.
  3. ALLOWED_ORGS stays consistent with ALLOWED_REPOS after override.
  4. Malformed entries (non-string, empty) are filtered out.
"""

import app_state
from adapters import github as gh
from common import settings as core_settings


# Snapshotted at module import time so tests can restore production
# state without duplicating the literal values (which would couple
# tests to whatever org / login is currently shipping in the open-
# source defaults).
_SNAPSHOT_REPOS = set(gh.ALLOWED_REPOS)
_SNAPSHOT_FTU = dict(gh.FORK_TO_UPSTREAM)
_SNAPSHOT_ACCOUNT_RULES = list(gh._account_rules)


def _restore():
    """Reset module-level state to the snapshot taken at import. Each
    test's setup/teardown calls this so we never leak overrides
    across tests."""
    gh.ALLOWED_REPOS.clear()
    gh.ALLOWED_REPOS.update(_SNAPSHOT_REPOS)
    gh.ALLOWED_ORGS.clear()
    gh.ALLOWED_ORGS.update(
        r.split("/")[0] for r in _SNAPSHOT_REPOS if r.endswith("/*")
    )
    gh.FORK_TO_UPSTREAM.clear()
    gh.FORK_TO_UPSTREAM.update(_SNAPSHOT_FTU)
    gh._account_rules = list(_SNAPSHOT_ACCOUNT_RULES)


class TestApplyRepoOverrides:
    def setup_method(self):
        _restore()

    def teardown_method(self):
        _restore()

    def test_no_override_when_settings_empty(self, patched_server):
        # Settings are empty by default in the test fixture.
        app_state._apply_repo_overrides_from_settings()
        assert gh.ALLOWED_REPOS == _SNAPSHOT_REPOS
        assert gh.FORK_TO_UPSTREAM == _SNAPSHOT_FTU

    def test_allowed_repos_override(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            ["fizz/buzz", "myorg/*"],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.ALLOWED_REPOS == {"fizz/buzz", "myorg/*"}
        # ALLOWED_ORGS recomputed from the new wildcards only.
        assert gh.ALLOWED_ORGS == {"myorg"}

    def test_allowed_repos_filters_non_strings(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            ["good/repo", "", None, 42, "another/*"],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.ALLOWED_REPOS == {"good/repo", "another/*"}
        assert gh.ALLOWED_ORGS == {"another"}

    def test_empty_list_leaves_defaults_alone(self, patched_server):
        # An empty list means "use the fallback" -- don't wipe the
        # hardcoded set just because the user cleared the editor.
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS, [],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.ALLOWED_REPOS == _SNAPSHOT_REPOS

    def test_fork_to_upstream_override(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_FORK_TO_UPSTREAM,
            {"me/x": "upstream/x", "me/y": "upstream/y"},
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.FORK_TO_UPSTREAM == {
            "me/x": "upstream/x",
            "me/y": "upstream/y",
        }

    def test_fork_to_upstream_filters_invalid_entries(self, patched_server):
        # JSON preserves only string keys; we only need to defend
        # against empty-string keys/values (the UI's most likely
        # malformed input).
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_FORK_TO_UPSTREAM,
            {"valid/k": "valid/v", "": "x", "y": ""},
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.FORK_TO_UPSTREAM == {"valid/k": "valid/v"}

    def test_is_repo_allowed_respects_override(self, patched_server):
        # After override, original repos no longer pass the allow-list;
        # the override targets do.
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ALLOWED_REPOS,
            ["fizz/buzz", "neworg/*"],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.is_repo_allowed("fizz/buzz") is True
        assert gh.is_repo_allowed("neworg/anything") is True
        # Pre-override defaults no longer pass. We pick one from the
        # original snapshot so the assertion stays correct even if the
        # open-source defaults change down the line.
        prior_default = next(
            (r for r in _SNAPSHOT_REPOS if "/" in r and not r.endswith("/*")),
            None,
        )
        if prior_default and prior_default not in {"fizz/buzz"}:
            assert gh.is_repo_allowed(prior_default) is False


class TestAccountRules:
    """`gh_account_for_repo` is settings-driven: a fork user can map
    "company/" repos to one gh login and everything else to another,
    without touching code."""

    def setup_method(self):
        _restore()

    def teardown_method(self):
        _restore()

    def test_no_settings_falls_back_to_hardcoded(self, patched_server):
        # Empty settings -> hardcoded heuristic. The exact return
        # value depends on the maintainer's defaults; we only assert
        # the function returns SOMETHING (never raises, never empty).
        app_state._apply_repo_overrides_from_settings()
        out = gh.gh_account_for_repo("anyorg/anyrepo")
        assert isinstance(out, str)
        assert out  # non-empty

    def test_settings_rule_routes_repos_to_account(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES,
            [
                {"match": "company-x/", "account": "alice-work"},
                {"match": "", "account": "alice-personal"},
            ],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.gh_account_for_repo("company-x/runtime") == "alice-work"
        assert gh.gh_account_for_repo("company-x/anything") == "alice-work"
        # Catch-all picks up everything else.
        assert gh.gh_account_for_repo("oss-org/widgets") == "alice-personal"
        assert gh.gh_account_for_repo("personal/dotfiles") == "alice-personal"

    def test_first_matching_rule_wins(self, patched_server):
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES,
            [
                {"match": "special/", "account": "specialist"},
                {"match": "special-foo", "account": "wrong"},  # later, never reached
                {"match": "", "account": "default"},
            ],
        )
        app_state._apply_repo_overrides_from_settings()
        # Both first and second rules would match "special/" -- first
        # in the list wins.
        assert gh.gh_account_for_repo("special/foo") == "specialist"

    def test_rule_with_empty_account_is_skipped(self, patched_server):
        # A malformed rule (empty account) shouldn't be honored --
        # we'd return "" which is invalid as a gh login.
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES,
            [
                {"match": "x/", "account": ""},  # filtered out at startup
                {"match": "", "account": "fallback"},
            ],
        )
        app_state._apply_repo_overrides_from_settings()
        assert gh.gh_account_for_repo("x/whatever") == "fallback"

    def test_invalid_settings_value_does_not_crash(self, patched_server):
        # Non-list and dict-of-strings shouldn't break startup.
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES, "not-a-list",
        )
        app_state._apply_repo_overrides_from_settings()
        # Still falls back to hardcoded heuristic.
        out = gh.gh_account_for_repo("anyorg/repo")
        assert isinstance(out, str) and out

    def test_warns_when_multiple_accounts_but_no_rules(
        self, patched_server, capsys, monkeypatch,
    ):
        """Real incident reproduction: with the personal-name fallback
        gone, an install with two gh accounts and zero rules silently
        routes everything to the FIRST loaded account. The startup
        check must surface this loud and clear so the user knows to
        configure rules instead of seeing mysteriously-empty PR feeds.
        """
        # Conftest's _seed_github_test_repos seeds default rules; clear
        # them so we hit the warning branch.
        monkeypatch.setattr(gh, "_account_rules", [])
        # Two accounts loaded -> warning trigger.
        monkeypatch.setattr(gh, "_gh_tokens", {
            "alice": "fake-tok-1",
            "alice-work": "fake-tok-2",
        })
        # Settings have no account_rules entry -> fallback path runs.
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES, None,
        )
        app_state._apply_repo_overrides_from_settings()
        captured = capsys.readouterr()
        # Loud stderr warning citing both account names.
        assert "WARNING" in captured.err
        assert "account_rules is empty" in captured.err
        assert "alice" in captured.err and "alice-work" in captured.err

    def test_no_warning_when_single_account(
        self, patched_server, capsys, monkeypatch,
    ):
        """Single-account install (no ambiguity about which token to
        use) must not emit the multi-account warning -- it'd be noise
        for the open-source default case."""
        monkeypatch.setattr(gh, "_account_rules", [])
        monkeypatch.setattr(gh, "_gh_tokens", {"solo": "tok"})
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES, None,
        )
        app_state._apply_repo_overrides_from_settings()
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err

    def test_no_warning_when_rules_already_configured(
        self, patched_server, capsys, monkeypatch,
    ):
        """Rules present -> we know how to route; no warning even if
        multiple accounts are loaded."""
        monkeypatch.setattr(gh, "_gh_tokens", {
            "alice": "tok1", "alice-work": "tok2",
        })
        patched_server._db.set_setting(
            core_settings.KEY_GITHUB_ACCOUNT_RULES,
            [{"match": "", "account": "alice"}],
        )
        app_state._apply_repo_overrides_from_settings()
        captured = capsys.readouterr()
        assert "WARNING" not in captured.err
