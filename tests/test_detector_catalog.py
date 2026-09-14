"""The catalogue must describe exactly the detectors that exist.

The dashboard kept its own copy of this and it drifted, in the direction that
matters: it was still telling visitors, beside named members of Congress, that

    Stock Outperformance -- "Trading returns significantly beat S&P 500"
    Loss Avoidance       -- "Statistically improbable success rate (80%+)"

long after both detectors had been disabled for making exactly those claims
without a price series or a statistical test. At the same time the six
detectors that carry a q-value were absent from the legend, the filter and the
explanation table, so every one of them rendered as "This anomaly requires
further investigation to understand its significance."

These tests are the thing that stops that happening again: they fail when a
detector is added, renamed or disabled and the catalogue is not updated with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.analysis.catalog import DETECTORS, as_dicts, live_detectors
from src.analysis.significance import NO_NULL_MODEL, NULL_SPECS
from src.api.main import app
from src.config import get_settings


@pytest.fixture
def client():
    return TestClient(app)


def _catalogued() -> set[str]:
    return {d.anomaly_type for d in DETECTORS}


class TestTheCatalogueMatchesTheDetectors:
    def test_every_detector_with_a_null_model_is_described(self):
        for spec in NULL_SPECS:
            assert spec.anomaly_type in _catalogued(), (
                f"{spec.anomaly_type} produces findings with a q-value and the site "
                "cannot explain what it is"
            )
        assert "cross_member_cluster" in _catalogued(), (
            "the cluster detector has its own null model and is not in NULL_SPECS"
        )

    def test_every_detector_without_a_null_model_is_described(self):
        for anomaly_type in NO_NULL_MODEL:
            assert anomaly_type in _catalogued(), anomaly_type

    def test_nothing_is_described_that_no_detector_writes(self):
        known = set(NO_NULL_MODEL) | {s.anomaly_type for s in NULL_SPECS} | {"cross_member_cluster"}
        assert _catalogued() - known == set(), (
            "the catalogue describes a detector that does not exist -- exactly how "
            "the old legend came to advertise three that had been deleted"
        )

    def test_a_disabled_detector_is_never_described(self):
        """The three disabled types must not reach a visitor as things we produce."""
        off = get_settings().disabled_anomaly_types_set
        assert off, "the disabled set is empty; this test would prove nothing"
        for anomaly_type in off:
            assert anomaly_type not in {d.anomaly_type for d in live_detectors()}, (
                f"{anomaly_type} is disabled and still offered to readers"
            )

    def test_has_null_model_agrees_with_the_significance_module(self):
        """The badge on a card is derived, never asserted a second time."""
        for entry in as_dicts():
            assert entry["has_null_model"] == (entry["anomaly_type"] not in NO_NULL_MODEL)


class TestEveryDetectorStatesWhatItCannotShow:
    """`limits` is the field that keeps a pattern from reading as an accusation."""

    def test_nothing_is_described_without_its_limits(self):
        for detector in DETECTORS:
            assert detector.limits.strip(), detector.anomaly_type
            assert detector.means.strip(), detector.anomaly_type
            assert detector.source.strip(), detector.anomaly_type

    def test_no_description_claims_a_return_or_a_profit(self):
        """There are no prices in this dataset, so no wording may imply one.

        D10: STOCK Act filings disclose amount bands and no share counts. Any
        text on this site saying a member gained, profited, outperformed or beat
        a benchmark is describing something that was never computed.
        """
        banned = ("return", "profit", "gain", "outperform", "beat the market", "alpha")
        for detector in DETECTORS:
            text = f"{detector.name} {detector.means}".lower()
            for word in banned:
                assert word not in text, f"{detector.anomaly_type} says {word!r}"

    def test_no_description_claims_significance_without_a_null_model(self):
        """ "Statistically improbable" was the old wording, on a detector with no test."""
        for detector in DETECTORS:
            if detector.anomaly_type in NO_NULL_MODEL:
                text = f"{detector.means} {detector.name}".lower()
                assert "significan" not in text, detector.anomaly_type
                assert "improbable" not in text, detector.anomaly_type
                assert "unlikely" not in text, detector.anomaly_type


class TestTheEndpointServesIt:
    def test_types_endpoint_returns_the_live_catalogue(self, client):
        response = client.get("/api/anomalies/types")
        assert response.status_code == 200

        body = response.json()
        assert {e["anomaly_type"] for e in body} == {d.anomaly_type for d in live_detectors()}
        assert all(e["limits"] for e in body)

    def test_types_is_not_swallowed_by_the_anomaly_id_route(self, client):
        """`/{anomaly_id}` takes an int; registered first it would 422 this."""
        assert client.get("/api/anomalies/types").status_code == 200


class TestTheLoudestDetectorSaysWhyItIsLoud:
    """`lobbying_overlap` produced 629 findings in the first run that had
    lobbying data — four times `late_filing` and the largest category on the
    site by a wide margin.

    That is a property of lobbying, not of trading. A large company files
    quarterly, often through several registrants, and each filing opens a
    ±30-day window; for a company that lobbies continuously those windows cover
    most of the year, so almost any trade in its stock falls inside one.

    The permutation null is exactly the right guard -- a shuffled calendar hits
    those windows just as often, so the q-value stays high, and none of the 629
    passed FDR. But a reader looking at a count does not see that, and a count
    is what the page leads with. The limits text has to say so.
    """

    def _detector(self, anomaly_type):
        from src.analysis.catalog import DETECTORS

        found = next((d for d in DETECTORS if d.anomaly_type == anomaly_type), None)
        assert found is not None, f"{anomaly_type} is not in the catalogue"
        return found

    def test_it_warns_that_the_count_is_not_the_signal(self):
        limits = self._detector("lobbying_overlap").limits.lower()

        assert "q-value" in limits, (
            "the loudest detector on the site does not tell the reader to weigh "
            "its findings by the q-value rather than the count"
        )

    def test_it_explains_the_base_rate(self):
        limits = self._detector("lobbying_overlap").limits.lower()

        assert "quarterly" in limits or "most of the year" in limits, (
            "the limits text does not explain why this detector fires so often"
        )

    def test_it_still_states_the_attribution_limit(self):
        """The original caveat must survive: a lobbying filing names the issuer,
        never the member lobbied."""
        limits = self._detector("lobbying_overlap").limits.lower()

        assert "issuer" in limits and "member" in limits

    def test_every_detector_that_carries_a_null_model_mentions_a_caveat(self):
        """A blanket floor: an empty or one-clause limits field on a detector
        that publishes findings under a member's name is not acceptable."""
        from src.analysis.catalog import DETECTORS

        thin = [d.anomaly_type for d in DETECTORS if len(d.limits) < 40]

        assert not thin, f"these detectors state no meaningful limit: {thin}"
