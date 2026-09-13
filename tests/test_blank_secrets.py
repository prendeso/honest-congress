"""An environment variable set to "" is not an environment variable left unset.

GitHub expands `${{ secrets.X }}` for a secret that does not exist to the EMPTY
STRING, not to nothing. An empty value overrides a field default instead of
leaving it in place, and that turned a working default into a broken one:

    secret absent from env        ->  'honest-congress contact@example.com'
    workflow passes unset secret  ->  'honest-congress '

Measured against SEC, from this machine, seconds apart:

    'honest-congress '                      -> 403
    'honest-congress contact@example.com'   -> 200  (10,426 ticker records)

The failure was silent and expensive. The ticker register came back empty, so
`TickerResolver` resolved nothing, so every USASpending award and every LDA
lobbying filing was discarded as unresolvable. `contract_front_run` and
`lobbying_overlap` -- two detectors that need no API key at all -- sat
permanently dark while their workflow steps reported success in about a second
each. Two of the six null models that feed the significance layer went with
them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from src.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _settings(**overrides) -> Settings:
    return Settings(ADMIN_PASSWORD="unit-test-password", **overrides)


class TestABlankContactFallsBackToTheDefault:
    @pytest.mark.parametrize("blank", ["", "   ", "\n", "\t "])
    def test_blank_does_not_override_the_default(self, blank):
        assert _settings(SEC_CONTACT_EMAIL=blank).sec_contact_email == "contact@example.com"

    def test_a_real_address_still_wins(self):
        assert _settings(SEC_CONTACT_EMAIL="ops@example.org").sec_contact_email == (
            "ops@example.org"
        )

    def test_the_user_agent_always_carries_an_address(self):
        """The invariant SEC actually enforces. A User-Agent naming no contact
        is refused outright, so this is the property worth pinning rather than
        any particular default value."""
        from src.ingestion.sec_tickers import _user_agent

        agent = _user_agent()
        assert re.search(r"[^@\s]+@[^@\s]+\.[^@\s]+", agent), (
            f"User-Agent {agent!r} carries no contact address; SEC answers 403"
        )


class TestCredentialsAreStripped:
    """A secret pasted with a trailing newline authenticates as a subtly wrong
    string, and the upstream rejects it in a way that reads like a bad key."""

    @pytest.mark.parametrize(
        "field,alias",
        [
            ("fec_api_key", "FEC_API_KEY"),
            ("congress_gov_api_key", "CONGRESS_GOV_API_KEY"),
            ("lda_api_key", "LDA_API_KEY"),
        ],
    )
    def test_surrounding_whitespace_is_removed(self, field, alias):
        assert getattr(_settings(**{alias: "  abc123\n"}), field) == "abc123"

    @pytest.mark.parametrize(
        "field,alias",
        [
            ("fec_api_key", "FEC_API_KEY"),
            ("congress_gov_api_key", "CONGRESS_GOV_API_KEY"),
        ],
    )
    def test_blank_stays_blank_so_it_still_fails_loudly(self, field, alias):
        """These must NOT fall back to anything. Their default is already "",
        and the CLI exits 1 on a missing key -- which is the correct, loud
        behaviour. Only the contact address has a usable default to protect."""
        assert getattr(_settings(**{alias: "   "}), field) == ""


class TestNoWorkflowPassesABareSecret:
    """The other half of the fix, at the layer where it broke.

    Neither layer alone is load-bearing: config absorbs a blank that reaches it,
    and this stops one being sent.
    """

    def _workflow_files(self):
        files = sorted(WORKFLOWS.glob("*.yml"))
        assert files, "no workflows found -- this test would pass on nothing"
        return files

    def test_sec_contact_email_always_has_a_fallback(self):
        offenders = []
        for path in self._workflow_files():
            for number, line in enumerate(path.read_text().splitlines(), 1):
                stripped = line.strip()
                if not stripped.startswith("SEC_CONTACT_EMAIL:"):
                    continue
                if "||" not in stripped:
                    offenders.append(f"{path.name}:{number}: {stripped}")

        assert not offenders, (
            "these pass the secret with no fallback, so an unset secret becomes "
            "an empty string and overrides the working default:\n" + "\n".join(offenders)
        )

    def test_the_workflows_still_parse(self):
        """A fallback expression is easy to write in a way that breaks YAML."""
        for path in self._workflow_files():
            parsed = yaml.safe_load(path.read_text())
            assert parsed["jobs"], f"{path.name} has no jobs"

    def test_every_sec_backed_step_sets_the_variable(self):
        """Catches the opposite mistake: dropping the variable entirely from a
        step that needs it, which fails the same way."""
        needs_contact = {"ingest-contracts", "ingest-lobbying", "sync-industries"}
        missing = []

        for path in self._workflow_files():
            parsed = yaml.safe_load(path.read_text())
            for job in parsed.get("jobs", {}).values():
                for step in job.get("steps", []):
                    run = step.get("run") or ""
                    if not any(command in run for command in needs_contact):
                        continue
                    if "SEC_CONTACT_EMAIL" not in (step.get("env") or {}):
                        missing.append(f"{path.name}: {step.get('name')}")

        assert not missing, f"SEC-backed steps with no contact address: {missing}"
