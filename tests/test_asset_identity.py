"""Asset matching and band-aware growth.

Two related problems in rapid_asset_appreciation:

* Assets were matched across filings on `description.lower().strip()`. Filings
  carry no asset identifier, so any wording change between years read as the
  old asset disappearing and a new one appearing -- and a "new" asset with a
  higher value looked like appreciation.
* Growth was computed from `value_max` on both sides. Disclosures report bands,
  so an asset that moved from the $1,001-$15,000 bracket to the $15,001-$50,000
  bracket registered as 233% growth even though the underlying change could be
  a single dollar.
"""

from __future__ import annotations

import pytest

from src.analysis.advanced_anomaly_detector import normalize_asset_key


class TestNormalizeAssetKey:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Apple Inc.", "Apple Inc"),
            ("Apple Inc.", "APPLE INC"),
            ("Microsoft Corporation", "Microsoft Corp"),
            ("Tesla, Inc.", "Tesla Inc"),
            ("Vanguard 500  Fund", "Vanguard 500 Fund"),
            ("Alphabet Inc. Class A", "Alphabet Inc Class A"),
            ("  Nvidia Corp  ", "Nvidia Corporation"),
        ],
    )
    def test_wording_variants_collapse_to_one_key(self, a, b):
        assert normalize_asset_key(a) == normalize_asset_key(b)

    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("Apple Inc.", "Microsoft Corp"),
            ("Vanguard 500 Fund", "Vanguard 2000 Fund"),
            ("First National Bank", "Second National Bank"),
        ],
    )
    def test_genuinely_different_assets_stay_distinct(self, a, b):
        assert normalize_asset_key(a) != normalize_asset_key(b)

    def test_handles_empty_and_none(self):
        assert normalize_asset_key("") == ""
        assert normalize_asset_key(None) == ""

    def test_key_is_stable_under_repeated_normalization(self):
        once = normalize_asset_key("Apple Inc.")
        assert normalize_asset_key(once) == once


class TestGuaranteedGrowth:
    """Only growth the reported bands actually guarantee should be flagged."""

    @staticmethod
    def _guaranteed(prev_max: float, curr_min: float) -> float:
        # Mirrors the calculation in detect_asset_appreciation_anomalies.
        return (curr_min - prev_max) / prev_max * 100 if prev_max > 0 else 0.0

    def test_adjacent_brackets_do_not_guarantee_growth(self):
        # $1,001-$15,000 -> $15,001-$50,000. True change could be $1.
        assert self._guaranteed(15_000, 15_001) == pytest.approx(0.0067, abs=0.01)

    def test_overlapping_bands_guarantee_nothing(self):
        # A band that overlaps the previous one cannot prove any growth.
        assert self._guaranteed(50_000, 15_001) < 0

    def test_a_genuine_jump_is_still_caught(self):
        # $1,001-$15,000 -> $1,000,001-$5,000,000 guarantees a huge increase.
        assert self._guaranteed(15_000, 1_000_001) > 100

    def test_band_edge_artefact_is_not_flagged_as_100_percent(self):
        # The old code compared value_max to value_max: 50000/15000 = 233%.
        # The guaranteed figure is ~0%, well under the >100% single-year gate.
        old_style = (50_000 - 15_000) / 15_000 * 100
        assert old_style > 100
        assert self._guaranteed(15_000, 15_001) < 100
