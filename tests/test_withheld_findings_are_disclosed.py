"""The anomalies page must not withhold findings silently.

`/api/anomalies/` defaults to `include_below_fdr=false`, so findings that were
tested against a shifted-calendar null and failed correction are absent from
every default response. Measured on the live corpus that is about two thirds of
it -- and, because the six detectors carrying a null model are exactly the six
that failed, *every finding on the page is one no statistical test vouched for*.

None of that was said anywhere. The page showed the corpus-wide count from
`/summary` in a headline tile, listed the filtered subset underneath, and
captioned the list "Showing 20 of 4,139 anomalies" -- a figure the list could
not reach by paging to the end. A reader had no way to learn the remainder
existed, and no way to see it.

These tests assert the disclosure and the escape hatch are present. They are
deliberately about the page rather than the API: the API was already honest
(`q_value IS NULL OR q_value <= alpha`, with NULL meaning untested and a
documented opt-in flag). It was the page that dropped the difference.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


@pytest.fixture(scope="module")
def page() -> str:
    return TestClient(app).get("/anomalies").text


def test_the_page_can_request_the_withheld_findings(page: str):
    """Without this parameter there is no way to reach them at all."""
    assert "include_below_fdr=true" in page


def test_the_page_offers_the_reader_a_way_to_see_them(page: str):
    assert "toggleBelowFdr" in page
    assert "showBelowFdr" in page


def test_the_page_says_what_it_is_withholding_and_why(page: str):
    """A count alone is not a disclosure; it has to say what the count means."""
    assert "withheldByFdr" in page
    assert "false-discovery-rate correction" in page
    # The reader is told the shown findings are untested, not vindicated.
    assert "no null model" in page


def test_the_page_states_the_limitation_of_the_null(page: str):
    """A withheld finding is not a finding of nothing.

    The shift null cannot see a member whose trading follows a regular
    schedule -- shifting the calendar puts the trades back where they were.
    `tests/test_significance.py::test_shift_null_is_blind_to_evenly_spaced_events`
    pins that behaviour down; this asserts the reader is told about it.
    """
    assert "regular schedule" in page


def test_the_list_caption_counts_the_list_and_not_the_corpus(page: str):
    """`stats.totalAnomalies` is every finding ever detected, filters and FDR
    ignored. Captioning the list with it was a plain untruth."""
    # The caption is one line of markup; matching the line avoids tripping over
    # the spans nested inside it.
    lines = [
        line for line in page.splitlines() if 'Showing <span x-text="anomalies.length"' in line
    ]
    assert len(lines) == 1, "the list caption moved or multiplied -- re-point this test"
    caption = lines[0]
    assert "stats.totalAnomalies" not in caption
    assert "anomalyTotal" in caption


def test_the_headline_tile_admits_it_counts_more_than_the_list_shows(page: str):
    """The tile may keep the corpus-wide number -- it is true of the corpus --
    but it has to say so, or the mismatch reads as a bug in the arithmetic."""
    assert "including those that failed correction" in page
