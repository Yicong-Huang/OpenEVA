"""OpenAI Codex CLI agent implementation."""

from __future__ import annotations

import json
import subprocess

from common.agent import CliAgentBase, _extract_json, register_agent


class CodexAgent(CliAgentBase):
    id = "codex"
    name = "OpenAI Codex"
    binary = "codex"
    system_prompt_via_launch = False

    def launch_argv(self, name: str, *, system_prompt=None, prompt=None) -> list[str]:
        # Inline mode makes Codex coexist with OpenEVA's own terminal
        # scrollback. Context is folded into the first user turn by callers.
        argv = self._env_prefix() + [self.binary, "--no-alt-screen"]
        if prompt:
            argv.append(prompt)
        return argv

    def resume_argv(self, session_id: str) -> list[str]:
        return self._env_prefix() + [
            self.binary, "resume", "--no-alt-screen", session_id,
        ]

    def fetch_usage(self, days: int = 1) -> dict | None:
        # Codex has no stable local usage command. The shared gateway usage
        # parser still reports Codex spend when another provider exposes it.
        return None

    def analyze(self, prompt: str, *, model: str = "haiku", timeout: int = 120,
                allow_tools: bool = False) -> dict | None:
        cmd = [self.binary, "exec", "--json", "--sandbox",
               "workspace-write" if allow_tools else "read-only"]
        # `haiku` is the framework's Claude-oriented speed alias, not a Codex
        # model name. Let Codex use its configured default in that case.
        if model and model != "haiku":
            cmd += ["--model", model]
        cmd.append(prompt)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=timeout)
        except Exception:
            return None
        if proc.returncode != 0:
            return None
        messages = []
        for line in proc.stdout.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            item = event.get("item") if isinstance(event, dict) else None
            if isinstance(item, dict) and item.get("type") == "agent_message":
                messages.append(item.get("text", ""))
        result = "\n".join(value for value in messages if value)
        return _extract_json(result) if result else None


register_agent(CodexAgent())
