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
