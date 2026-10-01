""" "Over $1,000,000" wrapped across two lines is still the band.

A House PTR table cell prints the spouse/dependent top band as "Spouse/DC Over"
above "$1,000,000". Read with its line break the known band did not match, and
the generic path stored a floor of exactly $1,000,000. The large-trade rule is
strictly over $1M, so these never fired -- while the identical band read from
the text layer, on one line, did. Twelve such rows in 47 sampled disclosures,
eight of them Doris Matsui's.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.parsing.ptr_parser import PTRParser


@pytest.mark.parametrize(
    "cell",
    ["Over $1,000,000", "Over\n$1,000,000", "Spouse/DC Over\n$1,000,000", "Over  \n $1,000,000"],
)
def test_the_spouse_top_band_has_the_same_floor_however_it_wraps(cell):
    assert PTRParser()._parse_amount_range(cell) == (Decimal("1000001"), None)


def test_a_wrapped_ordinary_band_is_unchanged():
    assert PTRParser()._parse_amount_range("$1,000,001 -\n$5,000,000") == (
        Decimal("1000001"),
        Decimal("5000000"),
    )
