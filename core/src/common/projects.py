"""Project management core logic + project-registration framework.

Projects live in `data/eva.db` -- created via the UI / `eva-cli`, then
edited freely. Extensions that need a specific project to exist
unconditionally (e.g. an internal-tooling extension that hardcodes
PR routing into a project id) can DECLARE the project via
`register_project(...)` from their `src/seed.py`. The framework's
boot path then `INSERT OR IGNORE`s every registered row into the
projects table, so the declared projects are present on a fresh
install without requiring manual UI setup.

Existing DB rows always win; declarations are seed-only. Users who
edit a project's name / description / repo in the UI keep their
edits even if the same extension re-declares with different values
on a later boot.
"""

import app_state


def compute_project_stats(project_id):
    """Return {total, counts, progress} for a project."""
    return app_state._db.project_stats(project_id)


def create_project(project_id: str, *, name: str = "",
                   description: str = "", repo: str | None = None,
                   jira: str | None = None,
                   has_tickets: bool = False) -> dict:
    """Create a new project row. Raises `ValueError` if the id is
    empty / malformed, or if a project with that id already exists
    -- the UI relies on these to surface a 422 to the user instead
    of the silent INSERT-OR-IGNORE swallow the DB layer does."""
    pid = (project_id or "").strip()
    if not pid:
        raise ValueError("project_id is required")
    # Same validation as task ids: lower-case kebab so URLs stay
    # tidy and the id can serve as a tmux session name verbatim.
    import re as _re
    if not _re.fullmatch(r"[a-z0-9][a-z0-9-]*[a-z0-9]|[a-z0-9]", pid):
        raise ValueError(
            "project_id must be lowercase letters, digits, and hyphens "
            "(no leading/trailing hyphen)",
        )
    if app_state._db.get_project(pid) is not None:
        raise ValueError(f"project {pid!r} already exists")
    app_state._db.create_project(
        pid, name=(name or pid), description=description,
        repo=repo, jira=jira, has_tickets=has_tickets,
    )
    return get_project(pid)


def _enrich_project(project_id, proj):
    """Add computed fields to project data (tasks, progress, counts)."""
    from . import settings as _settings
    tasks = app_state.load_tasks(project_id)
    stats = compute_project_stats(project_id)
    hidden = _settings.get_hidden_projects()
    return {
        "id": project_id,
        **proj,
        "has_tickets": proj.get("has_tickets", False),
        "hidden": project_id in hidden,
        "tasks": tasks,
        "progress": stats["progress"],
        "task_counts": stats["counts"],
    }


def list_projects():
    """Return list of project dicts with stats.

    Each row carries a `hidden` flag derived from the
    `ui.hidden_projects` setting. Callers decide what to do with
    hidden rows -- the sidebar filters them out by default; the
    Settings UI lists them so the user can un-hide."""
    from . import settings as _settings
    hidden = _settings.get_hidden_projects()
    db_projects = app_state._db.list_projects()
    projects = []
    for proj in db_projects:
        pid = proj["id"]
        stats = compute_project_stats(pid)
        projects.append({
            "id": pid,
            "name": proj.get("name", pid),
            "description": proj.get("description", ""),
            "has_tickets": proj.get("has_tickets", False),
            "hidden": pid in hidden,
            "progress": stats["progress"],
            "total_tasks": stats["total"],
            "task_counts": stats["counts"],
        })
    return projects


def get_project(project_id):
    """Return enriched project dict with tasks, progress, counts.
    Returns None if project not found."""
    proj = app_state._db.get_project(project_id)
    if not proj:
        return None
    return _enrich_project(project_id, proj)


def get_graph(project_id):
    """Return dependency graph {nodes, edges, groups, has_tickets}.
    Returns None if project not found."""
    proj = app_state._db.get_project(project_id)
    if not proj:
        return None
    graph = app_state._db.dependency_graph(project_id)
    graph["has_tickets"] = proj.get("has_tickets", False)
    for node in graph["nodes"]:
        node.setdefault("id", node.get("task_id", ""))
    return graph


# ---------------------------------------------------------------
# Project-registration framework
# ---------------------------------------------------------------
#
# Mirrors the plugins / agents / certs registries: a module-level
# list + a registration entry point + a flush-to-DB helper called
# once at boot after extension `seed.py` files have run. Each
# extension's seed file imports `register_project` and calls it
# with whatever projects it expects to exist; the framework
# idempotently `INSERT OR IGNORE`s each into the DB.

_registered_projects: list[dict] = []
_seen_project_ids: set[str] = set()


def register_project(project_id: str, *, name: str = "",
                     description: str = "", repo: str | None = None,
                     jira: str | None = None,
                     has_tickets: bool = False,
                     design_doc: str | None = None,
                     umbrella_tickets: list | None = None) -> None:
    """Declare a project to seed into the DB on boot.

    Idempotent on `project_id` -- a re-import that re-calls this
    with the same id is a no-op. The declaration is seed-only:
    `flush_registered_to_db()` uses `INSERT OR IGNORE`, so a row
    already present (e.g. user-edited via the UI) keeps its values.

    Typical caller: an extension's `src/seed.py`, where the
    extension declares the project ids it expects PR / ticket
    sync rules (also extension-declared) to refer to.
    """
    pid = (project_id or "").strip()
    if not pid:
        return
    if pid in _seen_project_ids:
        return
    _seen_project_ids.add(pid)
    _registered_projects.append({
        "id": pid,
        "name": name or pid,
        "description": description,
        "repo": repo,
        "jira": jira,
        "has_tickets": has_tickets,
        "design_doc": design_doc,
        "umbrella_tickets": umbrella_tickets or [],
    })


def all_registered_projects() -> list[dict]:
    """Snapshot of declarations in registration order. Test surface."""
    return list(_registered_projects)


def reset_registry_for_tests() -> None:
    """Clear the project declaration registry. Per-test fixtures
    use this so a test that imports an extension's seed module
    doesn't leak its declarations into the next test."""
    _registered_projects.clear()
    _seen_project_ids.clear()


def flush_registered_to_db() -> int:
    """INSERT OR IGNORE every registered project into the DB.

    Returns the count of rows newly inserted (i.e. project ids that
    weren't already in the DB). Existing rows are left untouched,
    so user edits to name / description / repo via the UI survive
    re-runs.
    """
    written = 0
    for p in _registered_projects:
        if app_state._db.project_exists(p["id"]):
            continue
        try:
            app_state._db.create_project(
                project_id=p["id"],
                name=p["name"],
                description=p["description"],
                repo=p["repo"],
                jira=p["jira"],
                has_tickets=p["has_tickets"],
                design_doc=p["design_doc"],
                umbrella_tickets=p["umbrella_tickets"],
            )
            written += 1
        except Exception as e:
            print(f"[projects] failed to seed {p['id']!r}: {e}",
                  flush=True)
    return written
