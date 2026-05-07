"""Templating utilities.

Centralizes the Jinja2 environment so every route uses the same templates
directory and global filters. Routes import `templates` and call
`templates.TemplateResponse("name.html", {"request": request, ...})`.
"""

from pathlib import Path

from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
