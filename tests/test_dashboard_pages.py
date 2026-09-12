"""Smoke tests for the dashboard HTML pages.

These ensure every Jinja template parses and renders for an empty database.
They don't assert on visible content — that would be brittle against
markup changes — they only catch outright template breakage.
"""

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/", "/members", "/disclosures", "/trades", "/parsed", "/anomalies", "/admin"],
)
def test_dashboard_page_renders(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200
    assert "<html" in response.text.lower()
    # Every page should include the brand name somewhere — base.html
    # nav for the main pages, the admin top bar for /admin.
    assert "Honest Congress" in response.text


@pytest.mark.parametrize(
    "path",
    ["/", "/members", "/disclosures", "/trades", "/parsed", "/anomalies", "/admin"],
)
def test_no_unrendered_jinja_braces_in_alpine_directives(client, path):
    """Alpine directives must not contain literal `{{ }}`.

    The templates were migrated out of Python f-strings, where `{{ }}` was an
    escaped literal brace. Inside a Jinja `{% raw %}` block those pass through
    verbatim, so `x-data="{{ insights: [] }}"` reached the browser as invalid
    JavaScript and Alpine threw a SyntaxError -- silently blanking the section.
    """
    import re

    html = client.get(path, follow_redirects=False).text
    offenders = re.findall(r'(x-[a-z:.-]+|:[a-z-]+)="\{\{[^"]*"', html)

    assert not offenders, f"{path} has unrendered braces in Alpine directives: {offenders}"


def test_home_insights_grid_uses_the_page_scope(client):
    """The Key Insights grid must read `insights` from landingPage().

    It previously declared its own nested `x-data`, which shadowed the parent's
    `insights` array and called a `loadInsights()` that existed only on the
    parent -- so the grid always rendered empty.
    """
    html = client.get("/", follow_redirects=False).text

    assert 'x-for="insight in insights"' in html
    assert "landingPage()" in html
    # Exactly one x-data on the page: the top-level landingPage() scope.
    assert html.count("x-data=") == 1, "the insights grid must not declare a nested scope"


def _extract_inline_scripts(html: str) -> list[str]:
    """Return the bodies of inline <script> blocks (skipping src= includes)."""
    import re

    return [
        m.group(1)
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL)
    ]


@pytest.mark.parametrize(
    "path",
    ["/", "/members", "/disclosures", "/trades", "/parsed", "/anomalies", "/admin"],
)
def test_inline_javascript_parses(client, path):
    """Every page's inline JavaScript must be syntactically valid.

    The anomalies page shipped `'This member\\\\'s net worth...'` inside a Jinja
    {% raw %} block -- an escaped backslash followed by a terminating quote,
    which is a SyntaxError. It killed the whole Alpine component, so the page
    rendered but never initialized. Like the home page's `{{ insights: [] }}`,
    it came from the Python f-string to Jinja migration, where `\\\\` was the
    escape for a literal backslash.

    Server-side render tests cannot catch this, because the template renders
    fine -- the failure is in the browser.
    """
    import shutil
    import subprocess
    import tempfile

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available to syntax-check inline scripts")

    html = client.get(path, follow_redirects=False).text
    scripts = _extract_inline_scripts(html)
    assert scripts, f"{path} has no inline script to check"

    for index, body in enumerate(scripts):
        if not body.strip():
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(body)
            temp_path = fh.name

        result = subprocess.run(
            [node, "--check", temp_path], capture_output=True, text=True, timeout=30
        )
        assert result.returncode == 0, (
            f"{path} inline script #{index} is not valid JavaScript:\n{result.stderr}"
        )
