"""CongressGovClient tests with mocked HTTP.

Same pattern as test_quiverquant.py: patch `session.get` to return synthetic
JSON, assert the parsing logic produces the right shape and edge cases are
handled gracefully.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.ingestion.congress_gov import (
    UNITEDSTATES_LEGISLATORS_URL,
    CongressGovClient,
)


def _mock_response(payload, status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    if status_code >= 400:
        err = requests.exceptions.HTTPError(response=MagicMock(status_code=status_code))
        response.raise_for_status.side_effect = err
    else:
        response.raise_for_status.return_value = None
    return response


_SAMPLE_LEGISLATOR = {
    "id": {"bioguide": "A000001"},
    "name": {"first": "Alice", "last": "Example"},
    "terms": [
        {
            "type": "rep",
            "party": "Democrat",
            "state": "CA",
            "district": 12,
            "start": "2023-01-03",
            "end": "2099-01-03",  # far future, "in office"
        }
    ],
}


class TestNormalizeParty:
    """Pure helper, no HTTP — easy correctness lock."""

    @pytest.fixture
    def client(self):
        return CongressGovClient(api_key="dummy")

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Democrat", "D"),
            ("D", "D"),
            ("Republican", "R"),
            ("R", "R"),
            ("Independent", "I"),
            ("I", "I"),
            ("ID", "I"),
            ("Libertarian", "O"),
            ("", "O"),
            (None, "O"),
        ],
    )
    def test_normalizes_to_single_letter(self, client, raw, expected):
        assert client._normalize_party(raw or "") == expected


class TestFetchFromUnitedStates:
    @pytest.fixture
    def client(self):
        return CongressGovClient(api_key="")

    def test_parses_legislator_into_member_dict(self, client):
        with patch.object(
            client.session, "get", return_value=_mock_response([_SAMPLE_LEGISLATOR])
        ) as mock_get:
            members = client._fetch_from_unitedstates()

        mock_get.assert_called_once_with(UNITEDSTATES_LEGISLATORS_URL, timeout=30)
        assert len(members) == 1
        m = members[0]
        assert m["bioguide_id"] == "A000001"
        assert m["first_name"] == "Alice"
        assert m["chamber"] == "house"
        assert m["party"] == "D"
        assert m["state"] == "CA"
        assert m["district"] == "12"
        assert m["in_office"] is True

    def test_skips_legislators_with_no_terms(self, client):
        bad = {"id": {"bioguide": "X"}, "name": {"first": "x", "last": "y"}, "terms": []}
        with patch.object(client.session, "get", return_value=_mock_response([bad])):
            assert client._fetch_from_unitedstates() == []

    def test_skips_legislators_whose_term_ended(self, client):
        retired = {
            "id": {"bioguide": "R000001"},
            "name": {"first": "Retired", "last": "Member"},
            "terms": [
                {
                    "type": "rep",
                    "party": "Republican",
                    "state": "TX",
                    "district": 1,
                    "start": "2015-01-03",
                    "end": "2020-01-03",  # past
                }
            ],
        }
        with patch.object(client.session, "get", return_value=_mock_response([retired])):
            assert client._fetch_from_unitedstates() == []

    def test_senator_chamber_routing(self, client):
        senator = {
            **_SAMPLE_LEGISLATOR,
            "terms": [{**_SAMPLE_LEGISLATOR["terms"][0], "type": "sen", "district": None}],
        }
        with patch.object(client.session, "get", return_value=_mock_response([senator])):
            members = client._fetch_from_unitedstates()
        assert members[0]["chamber"] == "senate"
        assert members[0]["district"] is None

    def test_http_error_returns_empty(self, client):
        with patch.object(client.session, "get", side_effect=requests.RequestException()):
            assert client._fetch_from_unitedstates() == []


class TestGetCurrentMembers:
    @pytest.fixture
    def client(self):
        return CongressGovClient(api_key="")

    def test_caches_results(self, client):
        with patch.object(
            client.session, "get", return_value=_mock_response([_SAMPLE_LEGISLATOR])
        ) as mock_get:
            client.get_current_members()
            client.get_current_members()
        # Cache hit on the second call — no second HTTP request.
        assert mock_get.call_count == 1

    def test_chamber_filter_house(self, client):
        senator = {
            "id": {"bioguide": "S000001"},
            "name": {"first": "Sen", "last": "Senator"},
            "terms": [
                {
                    "type": "sen",
                    "party": "Independent",
                    "state": "VT",
                    "start": "2023-01-03",
                    "end": "2099-01-03",
                }
            ],
        }
        with patch.object(
            client.session,
            "get",
            return_value=_mock_response([_SAMPLE_LEGISLATOR, senator]),
        ):
            assert len(client.get_current_members(chamber="both")) == 2
            assert len(client.get_current_members(chamber="house")) == 1
            assert len(client.get_current_members(chamber="senate")) == 1
