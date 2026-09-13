"""Severity must be normalized on every write path.

Three detector families wrote three vocabularies into `Anomaly.severity`:
"HIGH"/"CRITICAL" from the advanced/extended/tier-2 detectors,
"high"/"medium"/"low" from the late-filing detector, and raw integer scores
4-10 from the large-trade and frequency detectors. Because the API filters with
`severity == value.lower()`, every "HIGH" row was invisible to `?severity=high`.

Normalization is enforced by a model validator rather than in persist_anomalies,
because TradeAnalyzer and WealthAnalyzer construct Anomaly() directly.
"""

from __future__ import annotations

import pytest

from src.db.models import Anomaly, Chamber, Member, Party, normalize_severity


class TestNormalizeSeverity:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("high", "high"),
            ("HIGH", "high"),
            ("High", "high"),
            ("  high  ", "high"),
            ("critical", "high"),
            ("CRITICAL", "high"),
            ("medium", "medium"),
            ("MEDIUM", "medium"),
            ("low", "low"),
            ("LOW", "low"),
        ],
    )
    def test_text_vocabularies(self, raw, expected):
        assert normalize_severity(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(10, "high"), (8, "high"), (7, "medium"), (5, "medium"), (4, "low"), (0, "low")],
    )
    def test_integer_scores(self, raw, expected):
        assert normalize_severity(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("10", "high"), ("8", "high"), ("5", "medium"), ("4", "low")],
    )
    def test_stringified_integer_scores(self, raw, expected):
        # The ORM stringifies ints into a String column, so these reach the DB.
        assert normalize_severity(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "weird", "urgent", object()])
    def test_unknown_values_fall_back_to_medium(self, raw):
        assert normalize_severity(raw) == "medium"

    def test_output_is_always_in_the_api_vocabulary(self):
        candidates = ["HIGH", "critical", 10, 4, "weird", None, "3", "low"]
        assert {normalize_severity(c) for c in candidates} <= {"low", "medium", "high"}


class TestModelEnforcesNormalization:
    @pytest.fixture
    def member(self, db_session):
        m = Member(
            bioguide_id="N000001",
            first_name="Norm",
            last_name="Alize",
            chamber=Chamber.SENATE,
            party=Party.REPUBLICAN,
            state="FL",
        )
        db_session.add(m)
        db_session.commit()
        return m

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("HIGH", "high"), ("CRITICAL", "high"), (10, "high"), (4, "low"), ("weird", "medium")],
    )
    def test_direct_construction_is_normalized(self, db_session, member, raw, expected):
        # This is the path TradeAnalyzer and WealthAnalyzer take -- they bypass
        # persist_anomalies entirely, so the model has to do the work.
        a = Anomaly(
            member_id=member.id,
            anomaly_type="large_trade",
            severity=raw,
            title=f"row for {raw}",
            description="test",
        )
        db_session.add(a)
        db_session.commit()
        db_session.refresh(a)

        assert a.severity == expected

    def test_stored_value_is_filterable_lowercase(self, db_session, member):
        db_session.add(
            Anomaly(
                member_id=member.id,
                anomaly_type="late_filing",
                severity="HIGH",
                title="uppercase input",
                description="test",
            )
        )
        db_session.commit()

        # The exact-match filter the API used to run must now find the row.
        found = db_session.query(Anomaly).filter(Anomaly.severity == "high").count()
        assert found == 1
