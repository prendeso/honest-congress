"""Admin panel — renders src/templates/admin.html.

Used to be ~390 lines of inline HTML+JS embedded in a Python string. The
HTML now lives alongside the dashboard templates; data is still fetched
client-side from the existing /api/* endpoints.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.templating import templates

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request) -> HTMLResponse:
    """Serve the admin panel at /admin."""
    response = templates.TemplateResponse(request, "admin.html")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
