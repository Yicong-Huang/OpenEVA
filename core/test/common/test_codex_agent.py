from agents.codex import CodexAgent


def test_codex_launch_uses_inline_terminal_and_first_prompt():
    agent = CodexAgent()
    argv = agent.launch_argv("task-1", system_prompt="ignored", prompt="hello")
    assert argv[-3:] == ["codex", "--no-alt-screen", "hello"]
    assert agent.system_prompt_via_launch is False


def test_codex_resume_uses_codex_session_id():
    argv = CodexAgent().resume_argv("0198abcd-session")
    assert argv[-4:] == ["codex", "resume", "--no-alt-screen", "0198abcd-session"]
