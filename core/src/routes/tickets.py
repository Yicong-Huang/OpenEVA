"""HTTP routes for the Tickets page (JIRA-backed, multi-instance)."""

from typing import Optional

from pydantic import BaseModel
from fastapi import HTTPException

import app_state
from common import settings as _settings
from common import tickets as _core
from utils import clamp_int


@app_state.app.get("/api/tickets")
def list_tickets(limit: int = 100):
    """Return cached tickets across every configured instance, plus
    metadata so the UI can show "0 instances configured" hints. Each
    ticket is view-enriched (parsed labels / components / fix_versions,
    plus a `category` prefix and `linked_tasks` reverse-lookup).

    `limit` is clamped to [1, 1000] -- a hostile / typo'd value can
    no longer ask the DB for an unbounded scan.
    """
    safe_limit = clamp_int(limit, 1, 1000)
    return {
        "tickets": _core.list_tickets(limit=safe_limit),
        "configured": _core.is_configured(),
        "instances": _core.public_instances(),
    }


# Detail route MUST come before the parametric `/{key}/session` etc.
# below or FastAPI's path matcher will swallow the literal segment.
@app_state.app.get("/api/tickets/{key}")
def get_ticket(key: str, instance_name: str = ""):
    """Fetch one cached ticket by `(instance_name, key)`. The detail
    panel uses this so it always sees fresh enrichment (parsed lists,
    `linked_tasks`) even after a sync rewrites the row."""
    out = _core.get_one(key, instance_name=instance_name)
    if not out:
        raise HTTPException(
            status_code=404,
            detail=f"ticket {key!r} not cached"
            + (f" for instance {instance_name!r}" if instance_name else ""),
        )
    return out


@app_state.app.get("/api/tickets/{key}/triage")
def triage_ticket(key: str, instance_name: str = ""):
    """Return a flaky-test triage report for the given ticket.

    Bundles owner / problem / referenced-files into a single payload
    so the Tickets page can render a "Triage" panel without N round
    trips. 404s when the key isn't cached so the route layer can ask
    the user to sync first.

    Phase-1: pure JIRA-field extraction (no git-blame yet). The
    `blame` field is a placeholder for the future git integration.
    """
    out = _core.triage(key, instance_name=instance_name)
    if not out:
        raise HTTPException(
            status_code=404,
            detail=f"ticket {key!r} not cached -- sync first"
            + (f" (instance {instance_name!r})" if instance_name else ""),
        )
    return out


@app_state.app.post("/api/tickets/sync")
def sync_tickets():
    """Trigger a fresh sync across every configured instance.
    Per-instance errors are captured in the response, not raised --
    one broken JIRA shouldn't fail the whole sync."""
    try:
        return _core.sync_all()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app_state.app.post("/api/tickets/sync/{instance_name}")
def sync_one_instance(instance_name: str):
    """Sync just one instance (e.g. when the user clicks "refresh"
    on a single instance card)."""
    instances = _core.list_instances()
    inst = next((i for i in instances if i["name"] == instance_name), None)
    if not inst:
        raise HTTPException(
            status_code=404,
            detail=f"JIRA instance {instance_name!r} not configured")
    try:
        return _core.sync_one(inst)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


class TicketTrackRequest(BaseModel):
    """Empty body or `{instance_name?}` -- the route accepts both so a
    naive `POST /api/tickets/KEY/track` with no JSON works."""
    instance_name: Optional[str] = None


@app_state.app.post("/api/tickets/{key}/track")
def track_ticket(key: str, body: TicketTrackRequest = None):
    """Ensure `key` is in the cache and return its enriched detail.

    If already cached -> returned immediately.
    If not -> probe configured JIRA instances; the first one that
    knows the key wins. 404 when no configured instance recognises
    the key (or no instances are configured at all).
    """
    instance_name = (body.instance_name if body else "") or ""
    try:
        out = _core.track(key, instance_name=instance_name)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    if out is None:
        # Better than the bare "not found": list which instances we
        # actually probed so the user can see whether the right JIRA
        # is even configured. Most common failure mode is having one
        # instance configured but trying to track a ticket from a
        # different one -- the message should tell the user exactly
        # that plus point at Settings.
        instances = _core.list_instances()
        names = [i["name"] for i in instances] if not instance_name else (
            [instance_name])
        if not instances:
            detail = (
                f"ticket {key!r} can't be tracked -- no JIRA instances "
                f"are configured. Open Settings -> JIRA to add one."
            )
        elif not instance_name:
            tried = ", ".join(names) if names else "(none)"
            hosts = ", ".join(
                f"{i['name']}={i['base_url']}" for i in instances
            )
            detail = (
                f"ticket {key!r} not found in any configured JIRA "
                f"instance (tried: {tried}). Configured hosts: "
                f"{hosts}. If this ticket lives on a different JIRA, "
                f"add that instance under Settings -> JIRA."
            )
        else:
            detail = (
                f"ticket {key!r} not found in JIRA instance "
                f"{instance_name!r}"
            )
        raise HTTPException(status_code=404, detail=detail)
    return out


class TicketSessionRequest(BaseModel):
    instance_name: Optional[str] = None
    custom_prompt: Optional[str] = None


@app_state.app.post("/api/tickets/{key}/session")
def open_ticket_session(key: str, body: TicketSessionRequest = None):
    """Spawn (or resume) an agent session bound to a ticket. The
    session's tmux name is `ticket-<INSTANCE>-<KEY>` (legacy
    `ticket-<KEY>` when no instance is provided).

    Optional `custom_prompt` is pasted into the session after launch
    -- powers the action-button registry (Fix flaky test, Bisect, ...).
    """
    instance_name = body.instance_name if body else ""
    instance_name = instance_name or ""
    custom_prompt = (body.custom_prompt if body else "") or ""
    try:
        return _core.open_session_for_ticket(
            key, instance_name=instance_name,
            custom_prompt=custom_prompt,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ---- Write-side routes (Phase 2) -------------------------------------
#
# These talk back to JIRA. ValueError -> 404 (instance/ticket missing
# or invalid input); RuntimeError -> 502 (JIRA returned 4xx/5xx).
# The HTTP boundary is the only place we translate the adapter's
# `RuntimeError` shape -- core helpers re-raise it verbatim.


class TicketCommentRequest(BaseModel):
    body: str
    instance_name: Optional[str] = None


@app_state.app.post("/api/tickets/{key}/comment")
def post_ticket_comment(key: str, body: TicketCommentRequest):
    """Post a comment on the ticket. Returns the JIRA-side response
    (`{id, author, ...}`) so the UI can append it optimistically."""
    instance_name = (body.instance_name or "")
    try:
        return _core.add_comment(
            key, body.body, instance_name=instance_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


@app_state.app.get("/api/tickets/{key}/transitions")
def get_ticket_transitions(key: str, instance_name: str = ""):
    """List the transitions the calling user can apply (e.g.
    `Resolved`, `Closed`). The UI populates the Resolve dropdown
    from this; transition ids vary per JIRA project so we can't
    hardcode them."""
    try:
        return {
            "transitions": _core.list_transitions(
                key, instance_name=instance_name),
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


class TicketTransitionRequest(BaseModel):
    transition_id: str
    instance_name: Optional[str] = None
    resolution: Optional[str] = ""
    comment: Optional[str] = ""


@app_state.app.post("/api/tickets/{key}/transition")
def post_ticket_transition(key: str, body: TicketTransitionRequest):
    """Apply a transition. Returns 200 even when JIRA returns 204 No
    Content -- the JIRA response is opaque, so the caller refreshes
    the ticket cache to see the new status."""
    instance_name = (body.instance_name or "")
    try:
        return _core.transition_issue(
            key, body.transition_id,
            instance_name=instance_name,
            resolution=body.resolution or "",
            comment=body.comment or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---- Instance config CRUD ----
#
# These are thin sugar over the generic settings table -- the user
# *could* edit `service.jira.instances` directly via the Settings UI,
# but a dedicated endpoint lets the Tickets page validate per-instance
# fields and return better errors.

class JiraInstance(BaseModel):
    name: str
    base_url: str
    auth_type: str = "basic"
    email: str = ""
    api_token: str
    jql: str = ""


@app_state.app.put("/api/tickets/instances/{name}")
def upsert_instance(name: str, body: JiraInstance):
    """Insert or update a JIRA instance config. The name in the URL
    wins over `body.name` -- frontend can leave either blank."""
    if body.auth_type not in ("basic", "bearer"):
        raise HTTPException(
            status_code=422,
            detail="auth_type must be 'basic' or 'bearer'")
    if not body.api_token:
        raise HTTPException(
            status_code=422, detail="api_token is required")
    if not body.base_url:
        raise HTTPException(
            status_code=422, detail="base_url is required")
    name = (name or body.name or "").strip()
    if not name:
        raise HTTPException(
            status_code=422, detail="instance name is required")
    raw = _settings.get_value(_settings.KEY_JIRA_INSTANCES) or []
    instances = [i for i in raw if isinstance(i, dict) and i.get("name") != name]
    instances.append({
        "name": name,
        "base_url": body.base_url.strip(),
        "auth_type": body.auth_type,
        "email": body.email.strip(),
        "api_token": body.api_token,
        "jql": body.jql.strip() or _settings.DEFAULT_JIRA_JQL,
    })
    _settings.set_value(_settings.KEY_JIRA_INSTANCES, instances)
    return {"ok": True, "name": name}


@app_state.app.delete("/api/tickets/instances/{name}")
def delete_instance(name: str):
    raw = _settings.get_value(_settings.KEY_JIRA_INSTANCES) or []
    out = [i for i in raw if isinstance(i, dict) and i.get("name") != name]
    if len(out) == len(raw):
        raise HTTPException(
            status_code=404,
            detail=f"JIRA instance {name!r} not configured")
    _settings.set_value(_settings.KEY_JIRA_INSTANCES, out)
    # Drop cached tickets for the deleted instance.
    app_state._db._conn.execute(
        "DELETE FROM tickets WHERE instance_name=?", (name,),
    )
    app_state._db._conn.commit()
    return {"ok": True}
