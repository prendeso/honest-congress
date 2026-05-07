"""Smoke tests for the dashboard HTML pages.

These ensure every Jinja template parses and renders for an empty database.
They don't assert on visible content — that would be brittle against
markup changes — they only catch outright template breakage.
"""

import os

import pytest
from fastapi.testclient import TestClient

# The app refuses to start in production mode without ADMIN_PASSWORD —
# unset both so the test client boots in dev mode.
os.environ.pop("ENV", None)
os.environ.pop("ADMIN_PASSWORD", None)

from src.api.main import app  # noqa: E402
from src.config import get_settings  # noqa: E402

get_settings.cache_clear()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/", "/members", "/disclosures", "/trades", "/parsed", "/anomalies"],
)
def test_dashboard_page_renders(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 200
    assert "<html" in response.text.lower()
    # Every page should include the shared header from base.html.
    assert "Honest Congress" in response.text
