"""Admin login is throttled, and the pipeline stays out of the web process.

Two gaps found in the pre-launch review:

- `POST /api/anomalies/admin/login` compared the password with `!=` and let a
  script guess as fast as the server answered.
- `/analyze`, `/regenerate`, `/full-refresh` and `/cleanup` rewrote the
  `anomalies` table from inside the web service, outside the concurrency group
  that keeps the GitHub Actions pipelines to one writer at a time -- and ran a
  different pipeline from `cli analyze`, with no purges and no significance.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.api import auth
from src.api.main import app
from src.config import Settings


def _settings(env: str, **extra) -> Settings:
    return Settings(ENV=env, ADMIN_PASSWORD="unit-test-password", **extra)


@pytest.fixture(autouse=True)
def _fresh_throttle():
    auth.login_throttle.reset()
    yield
    auth.login_throttle.reset()


@pytest.fixture
def client(monkeypatch):
    from src.api.routes.anomalies import admin

    monkeypatch.setattr(admin, "get_settings", lambda: _settings("production"))
    return TestClient(app)


LOGIN = "/api/anomalies/admin/login"


class TestLoginIsThrottled:
    def test_the_right_password_still_logs_in(self, client):
        r = client.post(LOGIN, json={"password": "unit-test-password"})
        assert r.status_code == 200
        auth.revoke_admin_token(r.json()["token"])

    def test_a_client_is_locked_out_after_repeated_failures(self, client):
        for _ in range(auth.MAX_FAILURES_PER_CLIENT):
            assert client.post(LOGIN, json={"password": "wrong"}).status_code == 401

        r = client.post(LOGIN, json={"password": "unit-test-password"})
        assert r.status_code == 429, "even the right password waits out the lockout"
        assert int(r.headers["Retry-After"]) > 0

    def test_rotating_the_claimed_address_does_not_dodge_the_global_limit(self):
        throttle = auth.LoginThrottle(clock=lambda: 100.0)
        for i in range(auth.MAX_FAILURES_OVERALL):
            throttle.record_failure(f"10.0.0.{i}")
        assert throttle.retry_after("192.168.1.1") is not None

    def test_the_lockout_expires(self):
        now = [0.0]
        throttle = auth.LoginThrottle(clock=lambda: now[0])
        for _ in range(auth.MAX_FAILURES_PER_CLIENT):
            throttle.record_failure("a")
        assert throttle.retry_after("a") is not None
        now[0] = auth.LOGIN_WINDOW_SECONDS + 1
        assert throttle.retry_after("a") is None

    def test_the_comparison_is_constant_time(self):
        assert auth.password_matches("abc", "abc") is True
        assert auth.password_matches("abd", "abc") is False
        assert auth.password_matches("", "abc") is False
        assert auth.password_matches("ünï", "ünï") is True


class TestThePipelineIsNotRunFromTheWebProcess:
    GATED = (
        "/api/anomalies/analyze",
        "/api/anomalies/regenerate",
        "/api/anomalies/full-refresh",
        "/api/anomalies/cleanup",
    )

    def test_the_gate_refuses_in_production(self, monkeypatch):
        monkeypatch.setattr(auth, "get_settings", lambda: _settings("production"))
        with pytest.raises(HTTPException) as exc:
            auth.refuse_pipeline_in_production()
        assert exc.value.status_code == 409

    def test_the_gate_can_be_opened_deliberately(self, monkeypatch):
        monkeypatch.setattr(
            auth,
            "get_settings",
            lambda: _settings("production", ALLOW_ADMIN_PIPELINE_ROUTES="true"),
        )
        assert auth.refuse_pipeline_in_production() is None

    def test_the_gate_is_open_in_development(self, monkeypatch):
        monkeypatch.setattr(auth, "get_settings", lambda: _settings("dev"))
        assert auth.refuse_pipeline_in_production() is None

    @pytest.mark.parametrize("path", GATED)
    def test_an_authenticated_admin_is_refused_in_production(self, monkeypatch, path):
        monkeypatch.setattr(auth, "get_settings", lambda: _settings("production"))
        token = auth.issue_admin_token()
        try:
            r = TestClient(app).post(path, headers={"X-Admin-Token": token})
        finally:
            auth.revoke_admin_token(token)
        assert r.status_code == 409, f"{path} ran the pipeline in production"

    @pytest.mark.parametrize("path", GATED)
    def test_an_anonymous_caller_still_gets_401_not_409(self, monkeypatch, path):
        """Authentication is checked first, so the gate reveals nothing to strangers."""
        monkeypatch.setattr(auth, "get_settings", lambda: _settings("production"))
        r = TestClient(app).post(path)
        assert r.status_code == 401
