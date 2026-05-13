"""Unit tests for `adapters/common.agent.py`.

The Agent layer abstracts the CLI binary Eva shells out to
(`claude` for OSS; extensions can register others, e.g. `agent`).
Subprocess invocation + output parsing live in
`common.agent.CliAgentBase`; tests exercise the parser shape +
argv contract using a concrete binary value, so the regex-sensitive
text parsing can evolve safely.

This file replaces the older `test_adapters_my_agent.py`: those tests
all covered functions that have moved from
`adapters.agent.*` into `agent.*` with identical semantics.
"""

import common
import subprocess
import json as _json_lib
from unittest.mock import patch, MagicMock

from common import agent as _agent


class _MyAgent(_agent.CliAgentBase):
    """Test stand-in matching the real extension MyAgent shape.
    We construct one locally rather than importing from any
    extension's `agents/` so this test stays runnable on a
    core-only checkout."""
    id = "agent"
    name = "Agent"
    binary = "agent"


_my_agent = _MyAgent()


class TestParseUsage:
    """Pure parser -- no subprocess involved."""

    def test_all_three_amounts_and_power_user_tier(self):
        text = """
            Daily: $12.34
            Weekly: $56.78
            Monthly: $900.12
            Plan: Power User
        """
        data = _agent._parse_usage(text)
        assert data["daily"] == "12.34"
        assert data["weekly"] == "56.78"
        assert data["monthly"] == "900.12"
        assert data["tier"] == "Power User"

    def test_no_dollar_prefix(self):
        """Newer binaries omit the `$` -- parser should still match."""
        data = _agent._parse_usage(
            "Daily: 4.50\nWeekly: 22.00\nMonthly: 88.00\nStandard"
        )
        assert data["daily"] == "4.50"
        assert data["weekly"] == "22.00"
        assert data["monthly"] == "88.00"
        assert data["tier"] == "Standard"

    def test_comma_separated_amounts(self):
        """Thousands separators aren't stripped -- the UI layer handles it."""
        data = _agent._parse_usage("Daily: $1,234.56\nMonthly: $9,999.99")
        assert data["daily"] == "1,234.56"
        assert data["monthly"] == "9,999.99"

    def test_empty_output_returns_all_nones(self):
        data = _agent._parse_usage("")
        assert data == {"daily": None, "weekly": None,
                        "monthly": None, "tier": None}

    def test_partial_output(self):
        data = _agent._parse_usage("Daily: 5.00")
        assert data["daily"] == "5.00"
        assert data["weekly"] is None
        assert data["monthly"] is None
        assert data["tier"] is None

    def test_ignores_unrelated_lines(self):
        text = "Hello world\nDaily: 1.23\nSome other log line\nStandard"
        data = _agent._parse_usage(text)
        assert data["daily"] == "1.23"
        assert data["tier"] == "Standard"

    def test_standard_tier_detected(self):
        assert _agent._parse_usage("Plan: Standard")["tier"] == "Standard"

    def test_power_user_takes_priority_over_standard_on_same_line(self):
        assert _agent._parse_usage(
            "Plan: Power User Standard"
        )["tier"] == "Power User"


class TestFetchUsage:
    @patch("common.agent.subprocess.run")
    def test_success_returns_parsed_dict(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Daily: 10.00\nWeekly: 50.00\nMonthly: 200.00\nStandard",
            stderr="",
        )
        data = _my_agent.fetch_usage()
        assert data == {
            "daily": "10.00", "weekly": "50.00",
            "monthly": "200.00", "tier": "Standard",
        }

    @patch("common.agent.subprocess.run")
    def test_subprocess_failure_returns_none(self, mock_run):
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="agent", timeout=15)
        assert _my_agent.fetch_usage() is None

    @patch("common.agent.subprocess.run")
    def test_generic_exception_returns_none(self, mock_run):
        mock_run.side_effect = OSError("binary missing")
        assert _my_agent.fetch_usage() is None

    @patch("common.agent.subprocess.run")
    def test_passes_days_arg_through(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="")
        _my_agent.fetch_usage(days=7)
        cmd = mock_run.call_args[0][0]
        # Argv shape: <binary> usage --days <N>. Binary is configurable.
        assert cmd == ["agent", "usage", "--days", "7"]

    @patch("common.agent.subprocess.run")
    def test_combines_stdout_and_stderr(self, mock_run):
        mock_run.return_value = MagicMock(
            stdout="Daily: 1.00", stderr="Power User",
        )
        data = _my_agent.fetch_usage()
        assert data["daily"] == "1.00"
        assert data["tier"] == "Power User"

    @patch("common.agent.subprocess.run")
    def test_custom_timeout_respected(self, mock_run):
        mock_run.return_value = MagicMock(stdout="", stderr="")
        _my_agent.fetch_usage(timeout=5)
        assert mock_run.call_args.kwargs["timeout"] == 5


class TestExtractJson:
    def test_parses_raw_object(self):
        assert _agent._extract_json('{"a": 1}') == {"a": 1}

    def test_parses_object_with_surrounding_text(self):
        text = 'Here is the analysis:\n{"b": 2}\nDone.'
        assert _agent._extract_json(text) == {"b": 2}

    def test_fenced_json(self):
        text = "```json\n{\"c\": 3}\n```"
        assert _agent._extract_json(text) == {"c": 3}

    def test_returns_none_for_garbage(self):
        assert _agent._extract_json("no braces here") is None

    def test_returns_none_for_empty(self):
        assert _agent._extract_json("") is None

    def test_returns_none_for_non_string(self):
        assert _agent._extract_json(42) is None
        assert _agent._extract_json(None) is None

    def test_uses_outermost_braces(self):
        text = '{"outer": {"inner": true}}'
        assert _agent._extract_json(text) == {"outer": {"inner": True}}

    def test_malformed_json_returns_none(self):
        assert _agent._extract_json('{invalid: json}') is None


def _mock_popen(stdout_text="", stderr_text="", returncode=0, timeout=False):
    mock_proc = MagicMock()
    if timeout:
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd="agent", timeout=60,
        )
    else:
        mock_proc.communicate.return_value = (stdout_text, stderr_text)
    mock_proc.returncode = returncode
    return MagicMock(return_value=mock_proc)


class TestAnalyze:
    def test_success_with_wrapped_result(self):
        inner = '{"is_boba": true, "summary": "Yes"}'
        envelope = _json_lib.dumps({"result": inner})
        with patch("common.agent.subprocess.Popen",
                   _mock_popen(stdout_text=envelope)):
            out = _my_agent.analyze("prompt")
        assert out == {"is_boba": True, "summary": "Yes"}

    def test_success_with_dict_result_field(self):
        envelope = _json_lib.dumps({"result": {"is_boba": False}})
        with patch("common.agent.subprocess.Popen",
                   _mock_popen(stdout_text=envelope)):
            out = _my_agent.analyze("prompt")
        assert out == {"is_boba": False}

    def test_raw_json_without_envelope(self):
        raw = '{"task_id": "x"}'
        with patch("common.agent.subprocess.Popen",
                   _mock_popen(stdout_text=raw)):
            out = _my_agent.analyze("prompt")
        assert out == {"task_id": "x"}

    def test_nonzero_exit_returns_none(self):
        with patch("common.agent.subprocess.Popen",
                   _mock_popen(stdout_text="", returncode=1, stderr_text="boom")):
            assert _my_agent.analyze("prompt") is None

    def test_timeout_returns_none(self):
        with patch("common.agent.subprocess.Popen", _mock_popen(timeout=True)):
            assert _my_agent.analyze("prompt") is None

    def test_generic_exception_returns_none(self):
        bad = MagicMock(side_effect=OSError("binary missing"))
        with patch("common.agent.subprocess.Popen", bad):
            assert _my_agent.analyze("prompt") is None

    def test_invokes_configured_binary(self):
        """Argv[0] is the agent's `binary`. A ClaudeAgent invokes
        `claude`, an MyAgent invokes `agent`. Callers never write
        the binary name themselves -- they go through the agent."""
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("prompt")
        cmd = mock_cls.call_args[0][0]
        assert cmd[0] == "agent"

    def test_tools_disabled_by_default(self):
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("prompt")
        cmd = mock_cls.call_args[0][0]
        assert "--tools" in cmd
        i = cmd.index("--tools")
        assert cmd[i + 1] == ""

    def test_tools_enabled_opt_in(self):
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("prompt", allow_tools=True)
        cmd = mock_cls.call_args[0][0]
        assert "--tools" not in cmd

    def test_model_arg_passed(self):
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("prompt", model="sonnet")
        cmd = mock_cls.call_args[0][0]
        assert "--model" in cmd
        assert cmd[cmd.index("--model") + 1] == "sonnet"

    def test_timeout_arg_passed(self):
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("prompt", timeout=15)
        mock_proc = mock_cls.return_value
        assert mock_proc.communicate.call_args.kwargs.get("timeout") == 15

    def test_prompt_fed_via_stdin(self):
        mock_cls = _mock_popen(stdout_text='{"ok": true}')
        with patch("common.agent.subprocess.Popen", mock_cls):
            _my_agent.analyze("my prompt text")
        mock_proc = mock_cls.return_value
        assert mock_proc.communicate.call_args.kwargs.get("input") == "my prompt text"

    def test_timeout_swallows_kill_failure(self):
        mock_proc = MagicMock()
        mock_proc.communicate.side_effect = subprocess.TimeoutExpired(
            cmd="agent", timeout=60,
        )
        mock_proc.kill.side_effect = OSError("no such process")
        mock_cls = MagicMock(return_value=mock_proc)
        with patch("common.agent.subprocess.Popen", mock_cls):
            assert _my_agent.analyze("prompt") is None
        mock_proc.kill.assert_called_once()


# ---------------------------------------------------------------
# argv helpers + registry + active selection
# ---------------------------------------------------------------

class TestLaunchArgv:
    def test_bare_launch(self):
        argv = _my_agent.launch_argv("my-tmux")
        assert argv == ["agent", "-n", "my-tmux"]

    def test_with_system_prompt(self):
        argv = _my_agent.launch_argv("s", system_prompt="bg")
        assert argv == ["agent", "-n", "s", "--append-system-prompt", "bg"]

    def test_with_positional_prompt(self):
        argv = _my_agent.launch_argv("s", prompt="/yh-foo")
        assert argv == ["agent", "-n", "s", "/yh-foo"]

    def test_with_both(self):
        argv = _my_agent.launch_argv("s", system_prompt="bg", prompt="/cmd")
        assert argv == [
            "agent", "-n", "s", "--append-system-prompt", "bg", "/cmd",
        ]


class TestResumeArgv:
    def test_returns_resume_subcommand(self):
        assert _my_agent.resume_argv("uuid-123") == ["agent", "resume", "uuid-123"]


class TestActiveAgentResolution:
    def test_falls_back_to_default_when_setting_unset(self,
                                                       patched_server,
                                                       monkeypatch):
        # Need the OSS default registered for the fallback to find it.
        # `_isolate_agent_registry` already snapshotted, so we're
        # free to register a stand-in.
        class Claude(_agent.CliAgentBase):
            id = "claude"
            name = "Claude Code"
            binary = "claude"
        _agent.reset_for_tests()
        _agent.register_agent(Claude())
        active = _agent.get_active_agent()
        assert active.id == "claude"

    def test_setting_picks_registered_alternate(self, patched_server):
        class Claude(_agent.CliAgentBase):
            id = "claude"; name = "Claude"; binary = "claude"
        class Agent(_agent.CliAgentBase):
            id = "agent"; name = "Agent"; binary = "agent"
        _agent.reset_for_tests()
        _agent.register_agent(Claude())
        _agent.register_agent(Agent())
        patched_server._db.set_setting(_agent.KEY_AGENT_IMPL, "agent")
        active = _agent.get_active_agent()
        assert active.id == "agent"

    def test_no_agents_raises(self, patched_server):
        _agent.reset_for_tests()
        import pytest
        with pytest.raises(RuntimeError, match="No agent registered"):
            _agent.get_active_agent()

    def test_register_is_idempotent(self):
        _agent.reset_for_tests()

        class Once(_agent.CliAgentBase):
            id = "once"; name = "Once"; binary = "x"

        a, b = Once(), Once()
        _agent.register_agent(a)
        _agent.register_agent(b)
        assert _agent.all_agents() == [a]
