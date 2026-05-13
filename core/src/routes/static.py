"""Static file serving: React frontend assets + SPA fallback."""

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

import app_state


# React frontend (built by Vite).
_FRONTEND_DIR = app_state.REPO_ROOT / "frontend" / "dist"
if _FRONTEND_DIR.exists():
    app_state.app.mount(
        "/assets",
        StaticFiles(directory=str(_FRONTEND_DIR / "assets")),
        name="frontend-assets",
    )
    for _f in ["favicon.svg", "icons.svg"]:
        _fp = _FRONTEND_DIR / _f
        if _fp.exists():
            @app_state.app.get(f"/{_f}")
            def _serve_static_file(f=str(_fp)):
                return FileResponse(f)


@app_state.app.get("/")
@app_state.app.get("/{path:path}")
def serve_react(path: str = ""):
    """Serve the React SPA for all non-API paths.

    For unknown paths under the reserved prefixes we return 404 instead
    of the SPA shell -- otherwise a typo'd `GET /api/foo` would 200 with
    an HTML body, hiding the fact that the endpoint doesn't exist.
    """
    if path.startswith("api/") or path.startswith("assets/"):
        raise HTTPException(status_code=404, detail="Not Found")
    if not _FRONTEND_DIR.exists():
        raise HTTPException(
            status_code=503,
            detail="Frontend not built. Run `cd frontend && npm run build`.",
        )
    return FileResponse(str(_FRONTEND_DIR / "index.html"))
