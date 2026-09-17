"""Settings routes: list, get, set, delete the persisted key-value store.

Backs the right-corner Settings UI. The frontend reads `/api/settings`
once on open, then PUTs individual keys as the user edits each field
so partial-saves work the same as bulk-saves.
"""

from typing import Any

from pydantic import BaseModel
from fastapi import HTTPException

import app_state
from common import settings as core_settings
from common import agent as _agent
import shutil


class SettingValue(BaseModel):
    value: Any


class EnabledAgentsBody(BaseModel):
    value: list[str]


@app_state.app.get("/api/settings")
def list_settings():
    """Return all settings as `{key: value}` (JSON-decoded)."""
    return {"settings": core_settings.list_all()}


@app_state.app.get("/api/settings/{key}")
def get_setting(key: str):
    """Return one setting value. 404 if the key is absent."""
    sentinel = object()
    value = core_settings.get_value(key, default=sentinel)
    if value is sentinel:
        raise HTTPException(status_code=404, detail=f"setting '{key}' not found")
    return {"key": key, "value": value}


@app_state.app.put("/api/settings/{key}")
def set_setting(key: str, body: SettingValue):
    """Upsert one setting. Body: `{"value": <any JSON>}`."""
    core_settings.set_value(key, body.value)
    return {"key": key, "value": body.value}


@app_state.app.delete("/api/settings/{key}")
def delete_setting(key: str):
    """Remove one setting. 404 when nothing was deleted."""
    if not core_settings.delete_value(key):
        raise HTTPException(status_code=404, detail=f"setting '{key}' not found")
    return {"ok": True}


# -- Agent selection (consumed by the Settings UI's Setup tab) --

@app_state.app.get("/api/agents")
def list_available_agents():
    """Available agent implementations plus the user's enabled set.

    `agents` is the registry (id + human name); `enabled` is the ids the
    user checked (resolved: filtered to registered, defaults to the sole
    default agent when unset). When `enabled` has 2+ ids the open-session
    UI prompts the user to pick one at launch time."""
    agents = [
        {
            "id": agent.id,
            "name": getattr(agent, "name", agent.id),
            "binary": agent.binary,
            "available": shutil.which(agent.binary) is not None,
        }
        for agent in _agent.all_agents()
    ]
    if not agents:
        return {"agents": [], "enabled": [], "selected": None}
    enabled = _agent.get_enabled_agent_ids()
    return {
        "agents": agents,
        "enabled": enabled,
        "selected": enabled[0] if len(enabled) == 1 else None,
    }


@app_state.app.put("/api/agents/enabled")
def set_enabled_agents(body: EnabledAgentsBody):
    """Set the agents available for launching new sessions."""
    try:
        enabled = _agent.set_enabled_agent_ids(body.value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"enabled": enabled}


# -- Repo allow-list resolver (consumed by the Settings UI's Repos tab) --

from common import repos as _core_repos


@app_state.app.get("/api/repos/resolved")
def resolve_repos():
    """Return the configured repo rules alongside the live list of
    repos those rules currently match (driven by the local prs table
    so wildcards expand to "repos we actually have data for")."""
    return _core_repos.resolve()


@app_state.app.get("/api/repos/detect-forks")
def detect_forks():
    """Scan loaded gh CLI accounts for forks whose upstream is in the
    configured allow-list. Returns a suggested fork->upstream map for
    the Settings UI to preview and apply."""
    return _core_repos.detect_forks()


@app_state.app.get("/api/plugins/enabled")
def list_plugin_enabled_flags():
    """Return `{plugin_id: enabled_bool}` for every plugin. Powers the
    frontend's per-plugin "render or hide" decision so disabled
    plugins don't keep polling or take screen real estate."""
    return {"plugins": core_settings.get_all_plugin_enabled()}
