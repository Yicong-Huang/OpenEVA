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


class SettingValue(BaseModel):
    value: Any


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
