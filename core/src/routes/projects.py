"""Project management routes."""

from fastapi import HTTPException
from pydantic import BaseModel

import app_state
from common.projects import (
    list_projects as core_list_projects,
    get_project as core_get_project,
    get_graph as core_get_graph,
    create_project as core_create_project,
    compute_project_stats,  # noqa: F401 -- re-exported via server.py
    _enrich_project as enrich_project,  # noqa: F401 -- re-exported via server.py
)
from common import settings as core_settings
from common.tasks import (  # noqa: F401 -- re-exported via server.py
    suggest_task_status,
    is_task_blocked,
    _validate_task_id,
)


class ProjectVisibility(BaseModel):
    hidden: bool


class ProjectCreate(BaseModel):
    id: str
    name: str = ""
    description: str = ""
    repo: str | None = None
    jira: str | None = None
    has_tickets: bool = False

@app_state.app.get("/api/projects")
def list_projects():
    """List all projects with stats."""
    return {"projects": core_list_projects()}


@app_state.app.post("/api/projects", status_code=201)
def create_project(body: ProjectCreate):
    """Create a new project. The id must be lowercase kebab-case;
    422 on conflict so the UI can show a clean error."""
    try:
        return core_create_project(
            body.id, name=body.name, description=body.description,
            repo=body.repo, jira=body.jira, has_tickets=body.has_tickets,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.get("/api/projects/{project_id}")
def get_project(project_id: str):
    """Get project detail with all tasks, progress, and dependency graph."""
    result = core_get_project(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@app_state.app.get("/api/projects/{project_id}/graph")
def get_dependency_graph(project_id: str):
    """Return nodes and edges for the task dependency graph."""
    result = core_get_graph(project_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return result


@app_state.app.post("/api/projects/{project_id}/visibility")
def set_project_visibility(project_id: str, body: ProjectVisibility):
    """Toggle a project's hidden flag (persisted in the
    `ui.hidden_projects` setting). Returns the updated list so the
    client can sync without a second request."""
    if app_state._db.get_project(project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    new_set = core_settings.set_project_hidden(project_id, body.hidden)
    return {"project_id": project_id, "hidden": body.hidden,
            "hidden_projects": sorted(new_set)}
