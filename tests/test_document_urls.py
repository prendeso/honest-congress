"""A Senate filing must keep its own URL, everywhere it is shown.

`_normalized_document_url` repairs House Clerk links by rebuilding them from the
document id, which works because for the House the id IS the filename. For a
Senate filing none of its branches matched and it fell through to the final
`return`, INVENTING a House Clerk URL for a document that lives at
efdsearch.senate.gov under a UUID and a path segment (`/view/ptr/`,
`/view/annual/`, `/view/paper/`) that cannot be derived from anything stored.

The stored URL was correct the whole time; this was fabricated at response time,
so every Senate filing on the site linked to a 404. Sampled against production:
100 of 100 disclosures came back pointing at the House Clerk, not one carried an
eFD URL, and 20 of them had Senate document ids.

`cli fix-urls` documents this exact hazard and scopes itself to the House for
it. The same reasoning belongs here, and did not.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.api.routes.disclosures import _normalized_document_url
from src.db.models import Disclosure

EFD = "https://efdsearch.senate.gov/search/view"
CLERK = "https://disclosures-clerk.house.gov/public_disc"


def _disclosure(**kwargs) -> Disclosure:
    defaults = dict(
        member_id=1,
        filing_year=2026,
        filing_type="PTR",
        filing_date=datetime(2026, 5, 1),
        document_id="S257795ae-e1b2-411d-b562-8fe4c2a4f2a1",
        is_ptr=True,
        parsed=True,
    )
    defaults.update(kwargs)
    return Disclosure(**defaults)


class TestSenateUrlsAreLeftAlone:
    @pytest.mark.parametrize("kind", ["ptr", "annual", "paper"])
    def test_every_efd_path_survives_untouched(self, kind):
        url = f"{EFD}/{kind}/257795ae-e1b2-411d-b562-8fe4c2a4f2a1/"
        d = _disclosure(document_url=url, is_ptr=(kind == "ptr"))

        assert _normalized_document_url(d) == url

    def test_a_senate_filing_is_never_given_a_house_clerk_url(self):
        # The exact production row. The fabricated URL 404s.
        d = _disclosure(document_url=f"{EFD}/ptr/257795ae-e1b2-411d-b562-8fe4c2a4f2a1/")

        assert "disclosures-clerk.house.gov" not in (_normalized_document_url(d) or "")

    def test_an_unknown_host_is_not_improved_by_guessing(self):
        url = "https://example.gov/some/other/store/abc"
        d = _disclosure(document_url=url)

        assert _normalized_document_url(d) == url


class TestHouseUrlsAreStillRepaired:
    """The behaviour the function exists for, unchanged."""

    def test_a_ptr_filed_under_the_annual_path_is_corrected(self):
        d = _disclosure(
            document_id="10078188",
            document_url=f"{CLERK}/financial-pdfs/2026/10078188.pdf",
            is_ptr=True,
        )

        assert _normalized_document_url(d) == f"{CLERK}/ptr-pdfs/2026/10078188.pdf"

    def test_an_annual_filed_under_the_ptr_path_is_corrected(self):
        d = _disclosure(
            document_id="10078188",
            document_url=f"{CLERK}/ptr-pdfs/2026/10078188.pdf",
            is_ptr=False,
        )

        assert _normalized_document_url(d) == f"{CLERK}/financial-pdfs/2026/10078188.pdf"

    def test_a_missing_url_is_still_rebuilt_from_the_document_id(self):
        d = _disclosure(document_id="10078188", document_url=None, is_ptr=True)

        assert _normalized_document_url(d) == f"{CLERK}/ptr-pdfs/2026/10078188.pdf"

    def test_a_correct_house_url_is_left_as_it_is(self):
        url = f"{CLERK}/ptr-pdfs/2026/10078188.pdf"
        d = _disclosure(document_id="10078188", document_url=url, is_ptr=True)

        assert _normalized_document_url(d) == url
