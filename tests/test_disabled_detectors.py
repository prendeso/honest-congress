"""Tests for the disabled-detector gate.

Three detectors emit findings they cannot support and are disabled by default:

* ``outperforming_trades`` benchmarks against a hardcoded flat 10% and computes
  "return" as (sells - buys) / buys with no position matching.
* ``perfect_timing`` never reads a price -- it counts (buy, sell) date pairs in a
  nested loop and divides by ``len(buys)``, so its "success rate" exceeds 100%.
* ``loss_avoidance`` increments its numerator and denominator on the same branch,
  so its rate is always exactly 1.0 and the ``> 0.8`` gate is always true.

These attach ethics-investigation language to named public officials, so the
gate exists to keep them out of the database and therefore out of the API.
"""

from __future__ import annotations

import pytest

from src.analysis import persist_anomalies
from src.config import Settings
from src.db.models import Anomaly, Chamber, Member, Party


@pytest.fixture
def member(db_session):
    m = Member(
        bioguide_id="T000001",
        first_name="Test",
        last_name="Member",
        chamber=Chamber.HOUSE,
        party=Party.DEMOCRAT,
        state="CA",
    )
    db_session.add(m)
    db_session.commit()
    return m


def _anomaly(member_id: int, anomaly_type: str) -> dict:
    return {
        "member_id": member_id,
        "anomaly_type": anomaly_type,
        "severity": "HIGH",
        "title": f"{anomaly_type} finding",
        "description": "test",
    }


DISABLED = ["outperforming_trades", "perfect_timing", "loss_avoidance"]


class TestDisabledAnomalyTypes:
    @pytest.mark.parametrize("anomaly_type", DISABLED)
    def test_disabled_type_is_not_persisted(self, db_session, member, anomaly_type):
        inserted = persist_anomalies(db_session, [_anomaly(member.id, anomaly_type)])

        assert inserted == 0
        assert db_session.query(Anomaly).filter(Anomaly.anomaly_type == anomaly_type).count() == 0

    def test_enabled_type_is_still_persisted(self, db_session, member):
        inserted = persist_anomalies(db_session, [_anomaly(member.id, "late_filing")])

        assert inserted == 1
        assert db_session.query(Anomaly).filter(Anomaly.anomaly_type == "late_filing").count() == 1

    def test_disabled_types_filtered_out_of_mixed_batch(self, db_session, member):
        batch = [_anomaly(member.id, t) for t in DISABLED]
        batch.append(_anomaly(member.id, "large_trade"))

        inserted = persist_anomalies(db_session, batch)

        assert inserted == 1
        persisted = {a.anomaly_type for a in db_session.query(Anomaly).all()}
        assert persisted == {"large_trade"}


class TestDisabledTypesSetting:
    def test_defaults_cover_the_three_price_dependent_detectors(self):
        assert Settings().disabled_anomaly_types_set == set(DISABLED)

    def test_committee_conflict_detector_is_off_by_default(self):
        assert Settings().committee_conflict_detector_enabled is False

    def test_empty_setting_disables_nothing(self):
        assert Settings(DISABLED_ANOMALY_TYPES="").disabled_anomaly_types_set == set()

    def test_setting_is_overridable_and_whitespace_tolerant(self):
        s = Settings(DISABLED_ANOMALY_TYPES="large_trade, late_filing ")
        assert s.disabled_anomaly_types_set == {"large_trade", "late_filing"}


class TestPersistDeduplication:
    """(member_id, anomaly_type, title) is a unique index as of c3a7f1d92b04.

    The existence check in persist_anomalies queries the database, which cannot
    see rows added earlier in the same un-flushed batch -- so duplicates within
    one call have to be filtered in Python or the whole commit fails.
    """

    def test_duplicate_within_one_batch_is_collapsed(self, db_session, member):
        batch = [_anomaly(member.id, "large_trade"), _anomaly(member.id, "large_trade")]

        inserted = persist_anomalies(db_session, batch)

        assert inserted == 1
        assert db_session.query(Anomaly).count() == 1

    def test_duplicate_across_calls_is_not_reinserted(self, db_session, member):
        persist_anomalies(db_session, [_anomaly(member.id, "large_trade")])
        inserted = persist_anomalies(db_session, [_anomaly(member.id, "large_trade")])

        assert inserted == 0
        assert db_session.query(Anomaly).count() == 1

    def test_distinct_titles_are_both_kept(self, db_session, member):
        first = _anomaly(member.id, "large_trade")
        second = _anomaly(member.id, "large_trade")
        second["title"] = "a different finding"

        inserted = persist_anomalies(db_session, [first, second])

        assert inserted == 2
