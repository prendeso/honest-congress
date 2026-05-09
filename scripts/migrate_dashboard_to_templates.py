"""One-shot migration: extract HTML from dashboard_v2.py / dashboard.py
into Jinja2 template files.

Each page in the old dashboard files is one big f-string:
    html = f\"\"\"
    <!DOCTYPE html>
    ...
    {HEADER_HTML}
    <main>...</main>
    {FOOTER_HTML}
    <script>function pageFn() {{...}}</script>
    \"\"\"

This script:
* Locates each `@router.get(...)` HTML page
* Extracts the f-string body
* Strips the outer DOCTYPE/html/head/body wrapper (now lives in base.html)
* Replaces `{HEADER_HTML}` / `{FOOTER_HTML}` / `{STYLES}` references
* Un-escapes `{{` → `{` and `}}` → `}` (Python f-string un-escape)
* Wraps `<script>` blocks in {% raw %} so Jinja doesn't try to parse JS
* Writes templates/<name>.html with `{% extends "base.html" %}` plus
  content and scripts blocks.

Run once and discard. Idempotent: writes to a fresh dir each run.
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
TEMPLATES = SRC / "templates"

# (route_name, source_file, route_path) — written to templates/<route_name>.html
PAGES = [
    ("home", SRC / "api/routes/dashboard_v2.py", "/"),
    ("members", SRC / "api/routes/dashboard_v2.py", "/members"),
    ("disclosures", SRC / "api/routes/dashboard_v2.py", "/disclosures"),
    ("trades", SRC / "api/routes/dashboard_v2.py", "/trades"),
    ("parsed", SRC / "api/routes/dashboard_v2.py", "/parsed"),
    # /anomalies in dashboard_v2 redirects to DASHBOARD_HTML in dashboard.py
    ("anomalies", SRC / "api/routes/dashboard.py", None),
]


def extract_page_body(source: str, route_path: str | None) -> str:
    """Find the HTML body associated with `@router.get(route_path)` and
    return its content. For dashboard.py, route_path is None and we grab
    the DASHBOARD_HTML literal."""
    if route_path is None:
        m = re.search(r'DASHBOARD_HTML\s*=\s*"""(.*?)"""', source, re.DOTALL)
        if not m:
            raise RuntimeError("DASHBOARD_HTML not found")
        return m.group(1)

    # Find: @router.get("PATH"  ...  html = f"""...""" ...
    pattern = re.compile(
        r'@router\.get\("' + re.escape(route_path) + r'"[^)]*\)'
        r'.*?'
        r'html\s*=\s*f"""(.*?)"""',
        re.DOTALL,
    )
    m = pattern.search(source)
    if not m:
        raise RuntimeError(f"Page body for {route_path!r} not found")
    return m.group(1)


def split_body_and_scripts(html: str) -> tuple[str, str]:
    """Split the page body into (visible HTML, trailing <script>...</script>).

    The Alpine.js component lives in a single <script> block at the bottom
    of every page — we need it in {% block scripts %} so that {% raw %}
    can wrap it without affecting the rest of the page.
    """
    # Match the LAST <script> ... </script> block at the bottom of the body.
    m = re.search(
        r"(.*?)(<script>(?:(?!</script>).)*?</script>)\s*</body>\s*</html>\s*$",
        html,
        re.DOTALL,
    )
    if m:
        body = m.group(1)
        script = m.group(2)
        # Drop body tag opener that lives above the body
        body = re.sub(r"<body[^>]*>", "", body)
        return body, script
    # No script block — just strip the html/head/body wrapper.
    body = re.sub(r"<body[^>]*>", "", html)
    body = re.sub(r"</body>\s*</html>\s*$", "", body)
    return body, ""


def strip_outer_html(body: str) -> str:
    """Remove DOCTYPE, <html>, <head>...</head>, and the <body> open/close."""
    # Drop everything up to and including </head>
    body = re.sub(r"^.*?</head>", "", body, count=1, flags=re.DOTALL)
    return body


def unescape_fstring_braces(text: str) -> str:
    """Turn `{{` → `{` and `}}` → `}`. Python f-strings escaped both."""
    return text.replace("{{", "{").replace("}}", "}")


def replace_template_includes(body: str) -> str:
    """Swap inline references to HEADER_HTML/FOOTER_HTML/STYLES for the
    base.html include points (they live in base.html now), and strip any
    standalone <header>/<footer> blocks since base.html provides both."""
    body = body.replace("{HEADER_HTML}", "")
    body = body.replace("{FOOTER_HTML}", "")
    body = body.replace("{STYLES}", "")
    # The original dashboard.py had its own <header> block hard-coded
    # without using the HEADER_HTML constant. Strip any such tags so they
    # don't duplicate the base.html nav.
    body = re.sub(r"<header[^>]*>.*?</header>", "", body, flags=re.DOTALL)
    body = re.sub(r"<footer[^>]*>.*?</footer>", "", body, flags=re.DOTALL)
    return body


def render_template(name: str, body: str, script: str, title: str) -> str:
    body = body.strip()
    script = script.strip()
    # Wrap content + script in {% raw %} blocks — pages contain Alpine.js
    # expressions like x-data="{{ insights: [] }}" which Jinja would
    # otherwise try to evaluate. We don't need any Jinja interpolation
    # inside these page bodies (data is fetched client-side via /api/*).
    parts = [
        '{% extends "base.html" %}',
        "",
        f"{{% block title %}}{title} &mdash; Honest Congress{{% endblock %}}",
        "",
        "{% block content %}",
        "{% raw %}",
        body,
        "{% endraw %}",
        "{% endblock %}",
    ]
    if script:
        parts += [
            "",
            "{% block scripts %}",
            "{% raw %}",
            script,
            "{% endraw %}",
            "{% endblock %}",
        ]
    return "\n".join(parts) + "\n"


PAGE_TITLES = {
    "home": "Home",
    "members": "Members",
    "disclosures": "Disclosures",
    "trades": "Stock Trades",
    "parsed": "Parsed Data",
    "anomalies": "Anomalies",
}


def main() -> None:
    TEMPLATES.mkdir(parents=True, exist_ok=True)
    for name, src_file, route_path in PAGES:
        source = src_file.read_text()
        try:
            html = extract_page_body(source, route_path)
        except RuntimeError as e:
            print(f"[SKIP] {name}: {e}")
            continue

        # 1. Strip the outer DOCTYPE / html / head / body open
        html = strip_outer_html(html)
        # 2. Strip leading whitespace introduced by Python indentation
        html = textwrap.dedent(html)
        # 3. Split visible body from trailing <script>
        body, script = split_body_and_scripts(html)
        # 4. Substitute include points and un-escape braces
        body = replace_template_includes(body)
        body = unescape_fstring_braces(body)
        script = unescape_fstring_braces(script)
        # 5. Render with Jinja2 wrapper
        title = PAGE_TITLES[name]
        out = render_template(name, body, script, title)

        path = TEMPLATES / f"{name}.html"
        path.write_text(out)
        print(f"[OK]   wrote {path.relative_to(ROOT)}  ({len(out):,} chars)")


if __name__ == "__main__":
    main()
