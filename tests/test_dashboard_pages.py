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
