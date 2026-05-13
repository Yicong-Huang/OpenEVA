"""Adapters: the only place in Eva that talks to external systems.

Each adapter wraps ONE external CLI/API (gh, Slack, tmux, agent, ...) and
exposes Python-native types. Adapters have no dependency on `core/`,
`routes/`, or `eva_db`; they are callable from any layer.

The goal is to keep `core/` free of `subprocess.run`, raw network calls,
and "CLI-shaped" conventions. When a core function needs gh data it
imports from `adapters.github`; when a core function needs tmux state it
imports from `adapters.tmux`. That way core stays testable by mocking
one adapter interface instead of half a dozen scattered subprocess
calls.
"""
