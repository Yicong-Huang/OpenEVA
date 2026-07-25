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
        assert data == {
            "daily": None, "weekly": None, "monthly": None, "tier": None,
            "claude_cost": None, "claude_tokens": None,
            "codex_cost": None, "codex_tokens": None,
            "monthly_total": None, "monthly_budget": None,
            "claude_monthly": None,
        }

    def test_new_format_per_tool_cost_and_tokens(self):
        """Newer usage output groups spend under per-tool sections.
        The two identically-labelled `Cost (USD)` / `Total tokens`
        lines must land in the right bucket by section header."""
        text = """
AI Gateway Limits for user
  Current Month (Codex / Claude Code Model Serving)

Codex Usage Summary for user (last 7 days)
  Requests:      1,717
  Cost (USD):    $197.93
  Total tokens:  233,139,581

Claude Code Usage Summary for user (last 7 days)
  Cost (USD):    $1,335.84
  Total tokens:  1,397,218,343

  Power User (No local limits - only Anthropic limits apply)
  Daily:   $198.36
  Weekly:  $198.36
  Monthly: $198.36
        """
        data = _agent._parse_usage(text)
        assert data["daily"] == "198.36"
        assert data["tier"] == "Power User"
        assert data["codex_cost"] == "197.93"
        assert data["codex_tokens"] == "233,139,581"
        assert data["claude_cost"] == "1,335.84"
        assert data["claude_tokens"] == "1,397,218,343"

    def test_gateway_monthly_total_takes_priority_over_claude_section(self):
        """The AI Gateway Budgets block reports the account-wide monthly
        total, which must drive the headline `monthly` -- not the
        Claude-Code-only `Monthly:` line further down."""
        text = """
  Monthly Budget
  You have spent $7,459 out of your $15,000 monthly budget.

Claude Code Usage Summary for user (last 1 day)
  Cost (USD):    $729.42
  Total tokens:  662,688,776
  Daily:   $308.11
  Weekly:  $1,551.37
  Monthly: $6,863.74
        """
        data = _agent._parse_usage(text)
        assert data["monthly"] == "7,459"          # account-wide total
        assert data["monthly_total"] == "7,459"
        assert data["monthly_budget"] == "15,000"
        assert data["claude_monthly"] == "6,863.74"  # breakdown slice
        assert data["daily"] == "308.11"
        assert data["weekly"] == "1,551.37"

    def test_no_gateway_block_falls_back_to_claude_monthly(self):
        """Without the gateway block, `monthly` mirrors the Claude Code
        section's `Monthly:` (back-compat with the old headline)."""
        text = """
Claude Code Usage Summary for user (last 1 day)
  Daily:   $10.00
  Monthly: $200.00
        """
        data = _agent._parse_usage(text)
        assert data["monthly"] == "200.00"
        assert data["monthly_total"] is None
        assert data["monthly_budget"] is None
        assert data["claude_monthly"] == "200.00"

    def test_old_format_leaves_new_fields_none(self):
        """Output without the per-tool sections keeps cost/token fields
        None so the UI just omits the Spend rows."""
        data = _agent._parse_usage("Daily: $5.00\nPower User")
        assert data["claude_cost"] is None
        assert data["codex_cost"] is None
        assert data["claude_tokens"] is None

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
            "claude_cost": None, "claude_tokens": None,
            "codex_cost": None, "codex_tokens": None,
            "monthly_total": None, "monthly_budget": None,
            "claude_monthly": "200.00",
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

def _strip_env_prefix(argv):
    if argv[0] != "env":
        return argv
    i = 1
    while i < len(argv) and "=" in argv[i]:
        i += 1
    return argv[i:]


class TestLaunchArgv:
    def test_bare_launch(self):
        argv = _my_agent.launch_argv("my-tmux")
        assert _strip_env_prefix(argv) == ["agent", "-n", "my-tmux"]

    def test_with_system_prompt(self):
        argv = _my_agent.launch_argv("s", system_prompt="bg")
        assert _strip_env_prefix(argv) == [
            "agent", "-n", "s", "--append-system-prompt", "bg",
        ]

    def test_with_positional_prompt(self):
        argv = _my_agent.launch_argv("s", prompt="/yh-foo")
        assert _strip_env_prefix(argv) == ["agent", "-n", "s", "/yh-foo"]

    def test_with_both(self):
        argv = _my_agent.launch_argv("s", system_prompt="bg", prompt="/cmd")
        assert _strip_env_prefix(argv) == [
            "agent", "-n", "s", "--append-system-prompt", "bg", "/cmd",
        ]


class TestResumeArgv:
    def test_transcript_uuid_uses_local_resume_flag(self):
        # Canonical 8-4-4-4-12 transcript UUID -> local `--resume`
        # (cloud-independent), not the cloud `resume` subcommand.
        uuid = "48a6fd81-7f37-4e5a-a820-72b04d79ceab"
        assert _strip_env_prefix(_my_agent.resume_argv(uuid)) == [
            "agent", "--resume", uuid,
        ]

    def test_numeric_cloud_id_uses_resume_subcommand(self):
        assert _strip_env_prefix(_my_agent.resume_argv("1777500864453")) == [
            "agent", "resume", "1777500864453",
        ]

    def test_non_uuid_name_uses_resume_subcommand(self):
        # A name/search term is not a UUID -> cloud `resume` path.
        assert _strip_env_prefix(_my_agent.resume_argv("uuid-123")) == [
            "agent", "resume", "uuid-123",
        ]


class _EnvAgent(_agent.CliAgentBase):
    """Stand-in for an agent that injects session env."""
    id = "envagent"
    name = "Env Agent"
    binary = "agent"
    session_env = {"FOO": "1", "BAR": "x"}


_env_agent = _EnvAgent()


class TestSessionEnvPrefix:
    """Agent session argv includes terminal color env plus optional
    vendor env."""

    def test_default_color_env_prefix(self):
        argv = _my_agent.launch_argv("s")
        assert argv[:4] == [
            "env", "COLORTERM=truecolor", "FORCE_COLOR=1",
            "TERM=xterm-256color",
        ]
        assert _strip_env_prefix(argv) == ["agent", "-n", "s"]

    def test_launch_prefixes_env_sorted(self):
        argv = _env_agent.launch_argv("s", system_prompt="bg")
        # Keys emitted sorted for deterministic argv.
        assert argv == [
            "env", "BAR=x", "COLORTERM=truecolor", "FOO=1",
            "FORCE_COLOR=1", "TERM=xterm-256color",
            "agent", "-n", "s", "--append-system-prompt", "bg",
        ]

    def test_resume_uuid_prefixes_env(self):
        uuid = "48a6fd81-7f37-4e5a-a820-72b04d79ceab"
        argv = _env_agent.resume_argv(uuid)
        assert argv == [
            "env", "BAR=x", "COLORTERM=truecolor", "FOO=1",
            "FORCE_COLOR=1", "TERM=xterm-256color",
            "agent", "--resume", uuid,
        ]

    def test_resume_cloud_id_prefixes_env(self):
        argv = _env_agent.resume_argv("1777500864453")
        assert argv == [
            "env", "BAR=x", "COLORTERM=truecolor", "FOO=1",
            "FORCE_COLOR=1", "TERM=xterm-256color",
            "agent", "resume", "1777500864453",
        ]


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


class TestNewSessionAgentSelection:
    """New sessions launch with `new_session_impl` (default: the active
    agent); resume routes off the per-session recorded agent, with an
    empty record falling back to the default agent."""

    def _register(self, *ids):
        _agent.reset_for_tests()
        for aid in ids:
            cls = type(
                f"_A_{aid.replace('-', '_')}",
                (_agent.CliAgentBase,),
                {"id": aid, "name": aid, "binary": aid},
            )
            _agent.register_agent(cls())

    def test_new_session_setting_honored(self, patched_server):
        self._register("claude", "alt")
        patched_server._db.set_setting(
            _agent.KEY_NEW_SESSION_AGENT_IMPL, "alt")
        assert _agent.get_agent_for_new_session().id == "alt"

    def test_new_session_defaults_to_active_agent(self, patched_server):
        # No new-session setting row -> falls back to the active agent
        # (the OSS default `claude`).
        self._register("claude")
        assert _agent.get_agent_for_new_session().id == "claude"

    def test_get_agent_by_id_returns_recorded(self, patched_server):
        self._register("claude", "alt")
        assert _agent.get_agent_by_id("alt").id == "alt"

    def test_get_agent_by_id_empty_falls_back_to_default(self, patched_server):
        # Legacy row (empty agent_impl) resumes with the default agent.
        self._register("claude")
        assert _agent.get_agent_by_id("").id == "claude"

    def test_get_agent_by_id_unknown_falls_back_to_default(self,
                                                           patched_server):
        self._register("claude")
        assert _agent.get_agent_by_id("nonexistent").id == "claude"
