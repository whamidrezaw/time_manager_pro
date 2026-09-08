from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parents[2]
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter(tags=["web"])


def _compute_asset_version() -> str:
    """Short digest of the shipped CSS/JS, used as a cache-busting query param.

    Computed once at import time. Without it the browser keeps serving the
    app.js/style.css it cached before the deploy, so users never see a new
    build — which would quietly swallow any front-end fix.
    """
    digest = hashlib.sha256()
    for name in ("style.css", "app.js", "referral.js"):
        path = STATIC_DIR / name
        if path.exists():
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


ASSET_VERSION = _compute_asset_version()

@router.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
async def root(request: Request):
    if request.method == "HEAD":
        return Response(status_code=200)
    return RedirectResponse(url="/webapp", status_code=302)

@router.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    favicon_path = STATIC_DIR / "favicon.ico"
    if favicon_path.exists():
        return FileResponse(str(favicon_path))
    raise HTTPException(status_code=404, detail="favicon_not_found")

@router.get("/webapp", response_class=HTMLResponse, include_in_schema=False)
async def render_webapp(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"asset_version": ASSET_VERSION},
        headers={"Cache-Control": "no-store"},
    )
