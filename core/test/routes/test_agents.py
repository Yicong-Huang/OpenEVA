"""Route tests for the agent-selection endpoints (`/api/agents`,
`/api/agents/enabled`) that back the Settings UI's Agents section.

The agent registry is snapshotted/restored per test by the autouse
`_isolate_agent_registry` fixture, so registering stand-ins here is safe.
"""

from common import agent as _agent


def _register(*ids):
    _agent.reset_for_tests()
    for aid in ids:
        cls = type(
            f"_R_{aid.replace('-', '_')}",
            (_agent.CliAgentBase,),
            {"id": aid, "name": aid.title(), "binary": aid},
        )
        _agent.register_agent(cls())


class TestListAgents:
    def test_lists_registry_and_default_enabled(self, client):
        _register("claude", "codex")
        r = client.get("/api/agents")
        assert r.status_code == 200
        body = r.json()
        assert {a["id"] for a in body["agents"]} == {"claude", "codex"}
        # No enabled row set -> resolves to the single default agent.
        assert body["enabled"] == ["claude"]

    def test_reflects_enabled_setting(self, client, patched_server):
        _register("claude", "codex")
        patched_server._db.set_setting(
            _agent.KEY_ENABLED_AGENT_IDS, ["codex", "claude"])
        body = client.get("/api/agents").json()
        assert body["enabled"] == ["codex", "claude"]


class TestSetEnabledAgents:
    def test_set_valid_subset(self, client):
        _register("claude", "codex")
        r = client.put("/api/agents/enabled", json={"value": ["claude", "codex"]})
        assert r.status_code == 200
        assert r.json()["enabled"] == ["claude", "codex"]
        # persisted + reflected on the GET
        assert client.get("/api/agents").json()["enabled"] == ["claude", "codex"]

    def test_empty_rejected(self, client):
        _register("claude")
        r = client.put("/api/agents/enabled", json={"value": []})
        assert r.status_code == 422

    def test_unknown_agent_rejected(self, client):
        _register("claude")
        r = client.put("/api/agents/enabled", json={"value": ["claude", "ghost"]})
        assert r.status_code == 422
        assert "ghost" in r.json()["detail"]

    def test_dedups_preserving_order(self, client):
        _register("claude", "codex")
        r = client.put("/api/agents/enabled",
                       json={"value": ["codex", "claude", "codex"]})
        assert r.json()["enabled"] == ["codex", "claude"]

    def test_repoints_default_into_enabled_set(self, client, patched_server):
        _register("claude", "codex")
        # Default starts at claude; enable only codex -> default must move
        # to codex so a non-prompting launch stays inside the enabled set.
        patched_server._db.set_setting(_agent.KEY_NEW_SESSION_AGENT_IMPL, "claude")
        client.put("/api/agents/enabled", json={"value": ["codex"]})
        assert _agent.get_agent_for_new_session().id == "codex"
