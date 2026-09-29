"""What a public launch needs on every page: the disclaimer, a way to report a
correction, a robots.txt, and baseline security headers."""

from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


def test_the_about_page_says_a_finding_is_not_an_allegation():
    r = client.get("/about")
    assert r.status_code == 200
    assert "not an allegation of wrongdoing" in r.text
    assert 'id="corrections"' in r.text


def test_every_page_carries_the_disclaimer_and_the_corrections_link():
    for path in ("/", "/members", "/anomalies", "/compliance", "/about"):
        body = client.get(path).text
        assert "not allegations of wrongdoing" in body, path
        assert "/about#corrections" in body, path


def test_the_corrections_link_has_a_template_to_land_on():
    from pathlib import Path

    template = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE" / "correction.md"
    assert template.exists()


def test_robots_keeps_crawlers_out_of_admin_and_the_api():
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert "Disallow: /admin" in r.text
    assert "Disallow: /api/" in r.text


def test_baseline_security_headers_are_sent():
    r = client.get("/health/live")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Referrer-Policy" in r.headers


def test_hsts_is_not_sent_outside_production():
    # The test app runs with ENV unset; HSTS on localhost would pin a
    # developer's browser to HTTPS for a host that does not serve it.
    assert "Strict-Transport-Security" not in client.get("/health/live").headers


def test_the_script_versions_are_pinned():
    from pathlib import Path

    base = (Path(__file__).resolve().parents[1] / "src" / "templates" / "base.html").read_text()
    assert "@3.x.x" not in base


def test_each_summary_tile_asks_for_the_count_its_label_describes():
    """The anomalies page labels its tile "every detection, including those that
    failed correction"; the home page's "Flags" tile is what the site shows."""
    from pathlib import Path

    templates = Path(__file__).resolve().parents[1] / "src" / "templates"
    anomalies = (templates / "anomalies.html").read_text()
    home = (templates / "home.html").read_text()

    assert "including those that failed correction" in anomalies
    assert "/api/anomalies/summary?include_below_fdr=true" in anomalies
    assert "fetch('/api/anomalies/summary')" in home
