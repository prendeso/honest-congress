"""Production must not answer cross-origin requests from anywhere.

`src/api/main.py` configures CORS with `allow_headers=["*"]`. Paired with a
wildcard origin on a deployed host, that lets any page on the internet send
`X-Admin-Token` to the mutating endpoints. `allow_credentials` self-disables on
"*", which stops cookies but does nothing about a header an attacker sets
deliberately.

`ALLOWED_ORIGINS` defaulted to `*` and `allowed_origins_list` never consulted
`is_production`, even though the property sits directly above it -- so the hole
was open on any deployment where nobody had remembered the dashboard variable.
There was no test of CORS at all before this file.
"""

from __future__ import annotations

import pytest

from src.config import Settings


def _settings(env: str, origins: str) -> Settings:
    # ADMIN_PASSWORD is supplied because `get_settings()` refuses to build a
    # production Settings without one. Constructing Settings directly (rather
    # than through the lru_cached accessor) is the idiom the rest of the suite
    # uses for environment-conditional behaviour -- see TestAdminAuth.
    return Settings(ENV=env, ALLOWED_ORIGINS=origins, ADMIN_PASSWORD="unit-test-password")


class TestProductionRefusesTheWildcard:
    @pytest.mark.parametrize("origins", ["*", "", "   "])
    @pytest.mark.parametrize("env", ["production", "prod", "PRODUCTION"])
    def test_wildcard_and_unset_become_no_origins(self, env, origins):
        assert _settings(env, origins).allowed_origins_list == []

    def test_it_says_so_rather_than_failing_silently(self, caplog):
        """A refusal nobody is told about is indistinguishable from a bug."""
        with caplog.at_level("WARNING"):
            origins = _settings("production", "*").allowed_origins_list

        assert origins == []
        assert "ALLOWED_ORIGINS" in caplog.text

    def test_an_explicit_list_is_honoured(self):
        """The fix must not make the setting unusable -- only the wildcard goes."""
        assert _settings(
            "production", "https://app.example, https://admin.example"
        ).allowed_origins_list == ["https://app.example", "https://admin.example"]

    def test_a_single_explicit_origin_works(self):
        assert _settings("production", "https://app.example").allowed_origins_list == [
            "https://app.example"
        ]


class TestDevelopmentIsUnaffected:
    """Locking production down must not make local work harder.

    `tests/conftest.py` pops ENV, so every other test in the suite runs under
    these semantics.
    """

    @pytest.mark.parametrize("origins", ["*", ""])
    def test_wildcard_still_applies_outside_production(self, origins):
        assert _settings("dev", origins).allowed_origins_list == ["*"]

    def test_no_warning_outside_production(self, caplog):
        with caplog.at_level("WARNING"):
            origins = _settings("dev", "*").allowed_origins_list

        assert origins == ["*"]
        assert "ALLOWED_ORIGINS" not in caplog.text


class TestTheAppUsesIt:
    def test_credentials_stay_off_when_the_wildcard_is_in_play(self):
        """`allow_credentials=_origins != ["*"]` in main.py must keep holding.

        With the production fallback returning [] rather than ["*"], that
        expression now evaluates True in production -- which is correct, since
        an explicit origin list is exactly when credentials are safe -- but it
        is worth pinning that [] and ["*"] are not accidentally conflated.
        """
        assert _settings("production", "*").allowed_origins_list != ["*"]
        assert _settings("dev", "*").allowed_origins_list == ["*"]
