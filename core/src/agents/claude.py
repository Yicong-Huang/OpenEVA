"""Claude Code agent -- the OSS default Agent implementation.

Wraps Anthropic's `claude` CLI. All argv / parsing logic lives in
`common.agent.CliAgentBase`; this module only declares the binary
name + id/name and registers the instance with the agent registry
on import.

Switching to a different agent shipped by an extension is one
settings change: `service.agent.impl = <agent-id>`. No code edit
required for callers; they go through `common.agent.get_active_agent`
which re-resolves on every call.
"""

from common.agent import CliAgentBase, register_agent


class ClaudeAgent(CliAgentBase):
    id = "claude"
    name = "Claude Code"
    binary = "claude"


register_agent(ClaudeAgent())
