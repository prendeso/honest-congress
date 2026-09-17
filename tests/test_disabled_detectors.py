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
        """A subset, because the setting now carries two populations.

        `DISABLED` is the three whose arithmetic is indefensible and which need
        price history that does not exist. `TestTheHeldDetectorsAreStillHeld`
        below covers the two that are merely unread. An equality here would
        force anyone holding a detector for review to weaken this assertion,
        and the three price-dependent ones would quietly stop being guarded."""
        assert set(DISABLED) <= Settings().disabled_anomaly_types_set

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


class TestPersistSurvivesADroppedConnection:
    """The analysis step held a connection for 92 minutes in the run of 2026-09-14.

    A drop at the end of a detector would throw away everything it found and
    take the step with it -- a detector that took a quarter of an hour to
    produce its findings, lost while writing them.

    No cache to rebuild here, which is what makes the retry simple: `seen` is
    local to each attempt and rebuilt by re-running it, and the rows the
    rollback expunged are re-created the same way.
    """

    def _dropped(self):
        from sqlalchemy.exc import OperationalError

        exc = OperationalError("COMMIT", {}, Exception("SSL error: unexpected eof"))
        exc.connection_invalidated = True
        return exc

    def _finding(self, member, title):
        return {
            "member_id": member.id,
            "anomaly_type": "late_filing",
            "severity": "MEDIUM",
            "title": title,
            "description": "test",
        }

    def test_the_findings_are_stored_after_one_drop(self, db_session, member):
        from unittest.mock import patch

        from src.analysis import persist_anomalies
        from src.db.models import Anomaly

        real = db_session.commit
        calls = {"n": 0}

        def commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._dropped()
            return real()

        with patch.object(db_session, "commit", side_effect=commit):
            inserted = persist_anomalies(
                db_session, [self._finding(member, "late: A"), self._finding(member, "late: B")]
            )

        assert inserted == 2
        assert db_session.query(Anomaly).count() == 2

    def test_repeated_drops_lose_the_batch_without_killing_the_run(self, db_session, member):
        """Reported as zero rather than raised: the next analysis re-derives them."""
        from unittest.mock import patch

        from src.analysis import persist_anomalies
        from src.db.models import Anomaly

        def always_dropped():
            raise self._dropped()

        with patch.object(db_session, "commit", side_effect=always_dropped):
            inserted = persist_anomalies(db_session, [self._finding(member, "late: C")])

        assert inserted == 0
        assert db_session.query(Anomaly).count() == 0

    def test_a_real_database_error_still_raises(self, db_session, member):
        from unittest.mock import patch

        from sqlalchemy.exc import OperationalError

        from src.analysis import persist_anomalies

        def broken():
            raise OperationalError("COMMIT", {}, Exception("syntax error"))

        with patch.object(db_session, "commit", side_effect=broken):
            with pytest.raises(OperationalError):
                persist_anomalies(db_session, [self._finding(member, "late: D")])


class TestTheHeldDetectorsAreStillHeld:
    """`wealth_vs_salary` and `rapid_asset_appreciation` are HELD, NOT CONDEMNED.

    Both read their roster from `members_with_annual_filings`, which gated on
    `Disclosure.filing_type == "FD"` -- a value ZERO of 3,900 stored rows carry.
    Only the Senate ingester ever wrote it, as a fallback that stopped firing
    once real report titles were stored. So both walked an empty roster and
    found nothing, silently, for months.

    That gate is fixed, which is exactly the problem: they publish again, and
    NOBODY HAS EVER READ WHAT THEY SAY. This project has published four
    confident false accusations against named members of Congress -- three
    checkers during the audit, and the wealth findings that had Craig Goldman
    gaining $15,008,502.50 in a year against a real figure of $551,001.

    THIS IS A TRIPWIRE, NOT A VERDICT. Deleting it is the correct thing to do --
    once somebody has read a sample of what these two produce and can say it is
    sound. That is a much lower bar than the three detectors above, which need
    price history that does not exist. Do not read this test as saying they are
    broken; it says nobody has looked.
    """

    HELD = ("wealth_vs_salary", "rapid_asset_appreciation")

    @pytest.mark.parametrize("anomaly_type", HELD)
    def test_it_is_disabled_by_default(self, anomaly_type):
        assert anomaly_type in Settings().disabled_anomaly_types_set, (
            f"{anomaly_type} was re-enabled. If its output has been audited, delete "
            "this test and say so in the commit. If not, it is about to publish "
            "accusations nobody has read."
        )

    @pytest.mark.parametrize("anomaly_type", HELD)
    def test_nothing_it_writes_reaches_the_database(self, db_session, member, anomaly_type):
        """The gate has to hold at persist time, not only in the catalogue.
        Hiding a type from `/api/anomalies/types` while still storing its rows
        would leave them reachable by an explicit filter."""
        persist_anomalies(
            db_session,
            [
                {
                    "member_id": member.id,
                    "anomaly_type": anomaly_type,
                    "severity": "high",
                    "title": f"A finding from {anomaly_type}",
                    "description": "Should never be stored while the type is held.",
                }
            ],
        )

        stored = db_session.query(Anomaly).filter(Anomaly.anomaly_type == anomaly_type).count()
        assert stored == 0

    def test_the_held_ones_are_not_confused_with_the_condemned_ones(self):
        """Five types share one setting for two very different reasons. Anyone
        reading only the list would reasonably assume all five were judged
        unfit, and either leave these off for ever or re-enable them without
        looking -- so the comment that separates them is load-bearing."""
        import inspect

        import src.config

        source = inspect.getsource(src.config)
        for anomaly_type in self.HELD:
            assert anomaly_type in source

        assert "HELD, NOT CONDEMNED" in source, (
            "the distinction between 'held pending review' and 'condemned as "
            "indefensible' was removed from src/config.py; without it the next "
            "reader cannot tell which of the five is which"
        )
