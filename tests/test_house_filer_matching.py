"""Half of all House filings never reached the database.

Both House sync paths matched a filer to a member with

    Member.last_name.ilike(d["last_name"]),
    Member.first_name.ilike(f"{d['first_name']}%"),     # backwards
    Member.chamber == Chamber.HOUSE,
    Member.state == d["state"],
    ... .first()                                        # unordered

The Clerk's index carries FULL LEGAL names and the roster carries COMMON ones,
so the prefix required the roster name to begin with the index name — when the
index name is the longer of the two. Measured against the live Clerk index and
the live API: **608 of 1,332 sitting-member House PTRs missing across 2024-26
(45.6%)**, annual type-O 40.8% and 45.1% for 2025 and 2024, and 178 of 439
sitting members with no filings at all.

And `.first()` has no ORDER BY, so eight filings — including a 2026 PTR for
$100,001–$250,000 — are published under **Nicholas Begich (D-AK), who
disappeared in a plane crash in 1972**, while the sitting Nicholas Begich III
shows none.

The fix is deliberately NOT `_match_senator` ported across. Run verbatim over
the House that algorithm returns *confident wrong answers* rather than
ambiguity, because both its exit branches hand the caller a single row:
"Michael A." Collins narrows to his FATHER, and "April McClain" Delaney's 19
PTRs land on John Delaney — former MD-06, with the district agreeing.

Every name in this file is real, taken from the live index and roster.
"""

from __future__ import annotations

import pytest

from src.ingestion.orchestrator import (
    IngestionOrchestrator,
    clerk_first_name,
    first_names_are_compatible,
    surname_keys,
)


class Roster:
    """A stand-in carrying only what the matcher reads."""

    def __init__(self, bioguide, first, last, state, district=None, in_office=True):
        self.bioguide_id = bioguide
        self.first_name = first
        self.last_name = last
        self.state = state
        self.district = district
        self.in_office = in_office


def matcher(members):
    orch = IngestionOrchestrator.__new__(IngestionOrchestrator)

    def index(sitting_only):
        out = {}
        for m in members:
            if sitting_only and not m.in_office:
                continue
            for key in surname_keys(m.last_name):
                out.setdefault((key, m.state.upper()), []).append(m)
        return out

    orch._house_current = index(True)
    orch._house_all = index(False)
    return orch


class TestTheClerkNameIsTheLongerOne:
    """The defect itself: the roster name never begins with the legal name."""

    @pytest.mark.parametrize(
        "index_first,roster_first",
        [
            ("Marjorie Taylor", "Marjorie"),
            ("Suzan K.", "Suzan"),
            ("Kelly Louise", "Kelly"),
            ("Michael T.", "Michael"),
            ("Rohit", "Ro"),
            ("Harold Dallas", "Harold"),
            ("Donald Sternoff", "Donald"),
            ("Richard Dean Dr", "Rich"),
        ],
    )
    def test_the_two_spellings_are_recognised_as_one_person(self, index_first, roster_first):
        assert first_names_are_compatible(index_first, roster_first)

    @pytest.mark.parametrize(
        "index_first,roster_first",
        [
            ("Marjorie Taylor", "Marjorie"),
            ("Suzan K.", "Suzan"),
            ("Kelly Louise", "Kelly"),
            ("Rohit", "Ro"),
            ("Harold Dallas", "Harold"),
            ("Donald Sternoff", "Donald"),
        ],
    )
    def test_the_predicate_that_was_there_matches_none_of_them(self, index_first, roster_first):
        """The deleted code encoded here, because a test that only imports the
        new helpers proves nothing about what the old ones did.

        `Member.first_name.ilike(f"{index_first}%")` is, in Python, roster
        `.startswith(index)` case-insensitively. Every pair above is a real
        filer whose filings this dropped.
        """
        assert not roster_first.casefold().startswith(index_first.casefold())
        assert first_names_are_compatible(index_first, roster_first)

    @pytest.mark.parametrize(
        "raw,cleaned",
        [
            ("Mark Dr", "Mark"),
            ("Derrick F Mr", "Derrick F"),
            ("Richard Dean Dr", "Richard Dean"),
            ('Charles J. "Chuck"', "Charles J."),
        ],
    )
    def test_clerk_junk_is_stripped_before_comparing(self, raw, cleaned):
        assert clerk_first_name(raw) == cleaned


class TestTierOneDoesNotConsultTheFirstName:
    """Its omission is load-bearing. These roster first names are unrelated to
    the legal ones, and a first-name test at tier 1 would drop all of them."""

    @pytest.mark.parametrize(
        "index_first,roster_first,last,state",
        [
            ("Elizabeth", "Lizzie", "Fletcher", "TX"),
            ("Scott Scott", "C.", "Franklin", "FL"),
            ("Greg", "W.", "Steube", "FL"),
            ("James D", "Jim", "Jordan", "OH"),
            ("Michael A.", "Mike", "Collins", "GA"),
        ],
    )
    def test_a_sitting_member_matches_on_surname_and_state_alone(
        self, index_first, roster_first, last, state
    ):
        m = matcher([Roster("X000001", roster_first, last, state, in_office=True)])

        assert m._match_representative(None, index_first, last, state) != []


class TestItRefusesTheConfidentWrongAnswer:
    """Both of these return exactly one candidate under `_match_senator`, so
    "refuse when ambiguous" never fires. They are the reason it was not ported."""

    def test_a_compound_surname_reaches_the_sitting_member_not_her_predecessor(self):
        """April McClain Delaney files as First="April McClain" Last="Delaney".
        Her roster surname is "McClain Delaney", so without the last-token alias
        she misses tier 1 and lands on John Delaney — former MD-06, and the
        district agrees, so nothing downstream would question it. 19 PTRs."""
        sitting = Roster("M001232", "April", "McClain Delaney", "MD", "06", in_office=True)
        former = Roster("D000620", "John", "Delaney", "MD", "06", in_office=False)

        result = matcher([sitting, former])._match_representative(
            None, "April McClain", "Delaney", "MD", "06"
        )

        assert [m.bioguide_id for m in result] == ["M001232"]

    def test_a_member_is_not_matched_to_his_father(self):
        """C000640 Michael Allen Collins is C001129's father: same first name,
        same middle name, same state, same party. Leading-token narrowing on
        "michael" picks the father and not the sitting "Mike"."""
        son = Roster("C001129", "Mike", "Collins", "GA", "10", in_office=True)
        father = Roster("C000640", "Michael Allen", "Collins", "GA", "08", in_office=False)
        doug = Roster("C001093", "Doug", "Collins", "GA", "09", in_office=False)

        result = matcher([son, father, doug])._match_representative(
            None, "Michael A.", "Collins", "GA", "10"
        )

        assert [m.bioguide_id for m in result] == ["C001129"]

    def test_a_trade_is_not_published_under_a_man_who_died_in_1972(self):
        """The live defect. Both are Nicholas Begich, AK, at-large; `.first()`
        on an unordered query picked the grandfather, and production shows him
        with 8 filings and the sitting member with none."""
        sitting = Roster("B001323", "Nicholas", "Begich", "AK", None, in_office=True)
        grandfather = Roster("B000315", "Nicholas", "Begich", "AK", None, in_office=False)

        result = matcher([grandfather, sitting])._match_representative(
            None, "Nicholas", "Begich", "AK", "00"
        )

        assert [m.bioguide_id for m in result] == ["B001323"]


class TestALoneSittingNamesakeIsNotAssumedToBeTheFiler:
    """Tier 1 matches on `(surname, state)` alone, and defends omitting the
    first name on the grounds that the key is unique across the sitting House.

    It is -- but that only rules out collisions BETWEEN SITTING MEMBERS. It says
    nothing about a FORMER member's filing landing on a sitting namesake, and
    there the lone hit is confidently wrong.

    Caught by the repair pass's dry run over the live database, which proposed
    moving David Scott's assets and liabilities onto Austin Scott while
    reporting nothing it could not decide.
    """

    def test_a_former_members_filing_is_not_moved_onto_a_sitting_namesake(self):
        """`(scott, GA)` has exactly one sitting member, so David Scott's own
        disclosure matched Austin Scott: different first name, different
        district, different person. Documents 30022801 and 10066567, the second
        carrying 2 assets and 4 liabilities."""
        austin = Roster("S001189", "Austin", "Scott", "GA", "8", in_office=True)
        david = Roster("S001157", "David", "Scott", "GA", "13", in_office=False)

        result = matcher([austin, david])._match_representative(None, "David", "Scott", "GA", "13")

        assert [m.bioguide_id for m in result] == ["S001157"]

    @pytest.mark.parametrize(
        "index_first,roster_first,district,index_district",
        [
            # The name disagrees and the district agrees: still tier 1's job.
            ("Elizabeth", "Lizzie", "7", "07"),
            ("Greg", "W.", "17", "17"),
            ("Scott Scott", "C.", "18", "18"),
        ],
    )
    def test_a_roster_nickname_alone_never_refuses_the_match(
        self, index_first, roster_first, district, index_district
    ):
        """The guard needs BOTH signals to disagree. These are the 16 PTRs whose
        roster first name is unrelated to the legal one -- refusing them on the
        name alone is the regression that made tier 1 skip names to begin with.
        """
        sitting = Roster("X000001", roster_first, "Surname", "TX", district, in_office=True)

        result = matcher([sitting])._match_representative(
            None, index_first, "Surname", "TX", index_district
        )

        assert [m.bioguide_id for m in result] == ["X000001"]

    def test_a_stale_district_alone_never_refuses_the_match(self):
        """Rich McCormick's index entry still says GA06 against a GA-7 term.
        The district disagrees and the name does not, so he is still matched --
        `_same_district` is a tiebreak, never a filter."""
        mccormick = Roster("M001218", "Rich", "McCormick", "GA", "7", in_office=True)

        result = matcher([mccormick])._match_representative(None, "Rich", "McCormick", "GA", "06")

        assert [m.bioguide_id for m in result] == ["M001218"]

    def test_an_unlabelled_district_cannot_disagree(self):
        """An index entry with no district supplies only one signal, and one
        weak signal must not refuse a match on its own."""
        sitting = Roster("X000002", "Lizzie", "Fletcher", "TX", "7", in_office=True)

        result = matcher([sitting])._match_representative(None, "Elizabeth", "Fletcher", "TX", "")

        assert [m.bioguide_id for m in result] == ["X000002"]


class TestTierTwoRequiresTheNameToAgree:
    def test_a_departed_member_still_matches_their_own_filings(self):
        """Greene, Green, Connolly, Manning, Sherrill and Waltz all left office
        after filing. 39 of the 906 PTRs resolve to non-sitting members and
        every one is correct."""
        greene = Roster("G000596", "Marjorie", "Greene", "GA", "14", in_office=False)

        result = matcher([greene])._match_representative(
            None, "Marjorie Taylor", "Greene", "GA", "14"
        )

        assert [m.bioguide_id for m in result] == ["G000596"]

    def test_an_incompatible_name_is_refused_rather_than_guessed(self):
        """A lone historical candidate is not enough. This is the branch
        `_match_senator` does not have, and the one that saves Delaney."""
        stranger = Roster("X000009", "Bartholomew", "Delaney", "MD", "06", in_office=False)

        result = matcher([stranger])._match_representative(
            None, "April McClain", "Delaney", "MD", "06"
        )

        assert result == []

    def test_an_unknown_filer_matches_nobody(self):
        m = matcher([Roster("X000001", "Joe", "Wilson", "SC", "02", in_office=True)])

        assert m._match_representative(None, "Hampton", "Redmond", "SC", "02") == []


class TestDistrictIsATiebreakNeverAFilter:
    def test_a_stale_clerk_district_does_not_drop_the_filing(self):
        """Rich McCormick's 7 PTRs carry StateDst GA06; his current term is
        GA-7. He moved districts and the Clerk's value is stale."""
        mccormick = Roster("M001218", "Rich", "McCormick", "GA", "07", in_office=True)

        result = matcher([mccormick])._match_representative(
            None, "Richard Dean Dr", "McCormick", "GA", "06"
        )

        assert [m.bioguide_id for m in result] == ["M001218"]

    @pytest.mark.parametrize("roster_district", [None, "0", "00"])
    def test_at_large_compares_equal_however_it_is_spelled(self, roster_district):
        """The Clerk emits "00"; congress_gov stores None for district 0."""
        orch = IngestionOrchestrator.__new__(IngestionOrchestrator)
        member = Roster("B001323", "Nicholas", "Begich", "AK", roster_district)

        assert orch._same_district(member, "00")


class TestSurnameAliases:
    @pytest.mark.parametrize(
        "roster_surname,expected",
        [
            ("McClain Delaney", ["mcclain delaney", "delaney"]),
            ("Wasserman Schultz", ["wasserman schultz", "schultz"]),
            ("Delaney", ["delaney"]),
        ],
    )
    def test_a_compound_surname_is_reachable_by_its_last_token(self, roster_surname, expected):
        assert surname_keys(roster_surname) == expected


class TestCandidateReportsAreNotMemberFilings:
    def test_the_annual_index_no_longer_returns_them(self):
        """Shipping the matcher alone is a regression: the broken match
        suppressed candidate reports by accident (24 of 1,694), and the
        corrected one matches 80 — newly attaching 56 candidate filings to
        member records."""
        from src.ingestion.house import HouseIngester

        xml = b"""<?xml version="1.0"?><FinancialDisclosure>
          <Member><Prefix/><First>Cori</First><Last>Bush</Last><Suffix/>
            <FilingType>C</FilingType><StateDst>MO01</StateDst>
            <FilingDate>01/02/2025</FilingDate><DocID>111</DocID></Member>
          <Member><Prefix/><First>Suzan K.</First><Last>DelBene</Last><Suffix/>
            <FilingType>O</FilingType><StateDst>WA01</StateDst>
            <FilingDate>01/03/2025</FilingDate><DocID>222</DocID></Member>
        </FinancialDisclosure>"""

        rows = HouseIngester()._parse_xml_index(xml, 2025)

        assert [r["document_id"] for r in rows] == ["222"]

    def test_the_suffix_the_clerk_publishes_is_returned(self):
        """Not used by the matcher — `Member` has no suffix column — but it is
        the only thing that tells the two Nicholas Begiches apart by name, and
        the repair pass needs it."""
        from src.ingestion.house import HouseIngester

        xml = b"""<?xml version="1.0"?><FinancialDisclosure>
          <Member><Prefix/><First>Nicholas</First><Last>Begich</Last><Suffix>III</Suffix>
            <FilingType>P</FilingType><StateDst>AK00</StateDst>
            <FilingDate>02/02/2026</FilingDate><DocID>333</DocID></Member>
        </FinancialDisclosure>"""

        rows = HouseIngester()._parse_ptr_xml_index(xml, 2026)

        assert rows[0]["suffix"] == "III"
