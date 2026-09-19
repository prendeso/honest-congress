"""The owner vocabulary, and the one branch that must not be "cleaned up".

#98 stopped an unread House owner being asserted as the member's, and left one
case alone: `_normalize_owner`'s final `else`, which answers "Self" for an owner
string it does not recognise. That looked like the same defect waiting to be
fixed, so it was measured before being changed.

Over 114 real House PTRs -- 1,556 owner cells -- the column holds exactly four
values:

    'SP'  706      ''  705      'JT'  123      'DC'  22

The unrecognised branch fires **zero times**. But `SenateHtmlParser` subclasses
`PTRParser`, and the Senate's eFD writes the words out:

    'Self'  'Spouse'

and **"Self" matches none of the tests above that `else`.** It is not "spouse",
not "joint", not "child". So the catch-all was the only thing reading it, and
returning None there -- the change that looked obviously right -- would have
made every Senate member's own trade unattributed. `attribution.held_by_member`
would then drop each one from every count-based detector: not counting somebody
else's trades as the member's, but failing to count the member's own at all,
for an entire chamber.

So "self" is matched explicitly and the catch-all no longer carries a value the
parsers produce. These tests are what stops it being removed.
"""

from __future__ import annotations

import pytest

from src.parsing.ptr_parser import PTRParser


@pytest.fixture
def owner():
    return PTRParser()._normalize_owner


class TestTheHouseVocabulary:
    """`SP` / `JT` / `DC`, and blank for the filer."""

    @pytest.mark.parametrize(
        ("code", "expected"),
        [("SP", "Spouse"), ("JT", "Joint"), ("DC", "Dependent Child")],
    )
    def test_the_printed_codes(self, owner, code, expected):
        assert owner(code) == expected
        assert owner(code.lower()) == expected

    def test_blank_is_the_filer(self, owner):
        # The form leaves the column empty for the filer. Blank is the document
        # saying "mine", not a value nobody could read.
        assert owner("") == "Self"
        assert owner("   ") == "Self"


class TestTheSenateVocabulary:
    """Spelled out, and `Self` is the case that matters."""

    def test_self_spelled_out_is_the_filer(self, owner):
        assert owner("Self") == "Self"
        assert owner("self") == "Self"
        assert owner("SELF") == "Self"

    def test_spouse_spelled_out(self, owner):
        assert owner("Spouse") == "Spouse"


class TestTheCatchAllCarriesNothing:
    """The measurement, turned into a guard.

    The first version of this reimplemented the branch logic inside the test
    and asked its own copy whether a value fell through. That checks a replica,
    not the code, and it passed happily with the explicit `self` branch deleted.
    So this reads the real function instead: every value the two chambers
    actually write must be named by a branch, and `ast` is how you ask that.
    """

    # Every value observed across 114 real House PTRs (1,556 owner cells) and
    # the vendored Senate filing. Blank is handled by its own early return.
    OBSERVED = ["SP", "JT", "DC", "Self", "Spouse"]

    @staticmethod
    def _literals_the_function_compares_against() -> set[str]:
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(PTRParser._normalize_owner).lstrip())
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for side in [node.left, *node.comparators]:
                    if isinstance(side, ast.Constant) and isinstance(side.value, str):
                        found.add(side.value.lower())
        return found

    @pytest.mark.parametrize("value", OBSERVED)
    def test_every_observed_value_is_named_by_a_branch(self, value):
        """Not "resolves correctly" -- *named*. A value that only resolves
        because the catch-all happens to return the right answer is one
        deletion away from resolving wrongly, which is exactly what the
        Senate's "Self" was before this."""
        named = self._literals_the_function_compares_against()
        low = value.lower()

        assert any(low == n or n in low or low in n for n in named), (
            f"{value!r} is a value the parsers really produce, and no branch "
            f"names it -- it survives only on the catch-all. Named: {sorted(named)}"
        )

    def test_self_in_particular(self):
        """Called out on its own because it is the one that was missing, and
        the one whose absence costs a whole chamber."""
        assert "self" in self._literals_the_function_compares_against()


class TestTheChangeThatWouldBreakASenate:
    """A regression guard written before the regression.

    Stated as the consequence rather than the implementation, so it still holds
    if the function is rewritten: whatever `_normalize_owner` becomes, the
    Senate's own word for the filer has to survive it all the way to
    `attribution`, which is what decides whether the trade is counted.
    """

    def test_a_senate_self_row_is_still_the_members_own(self, owner):
        from src.analysis.attribution import held_by_member

        class _Row:
            pass

        row = _Row()
        row.owner = owner("Self")

        assert held_by_member(row), (
            "the Senate writes 'Self'; if that stops resolving, every senator's "
            "own trade drops out of every count-based detector"
        )

    def test_a_senate_spouse_row_is_still_excluded(self, owner):
        from src.analysis.attribution import held_by_member

        class _Row:
            pass

        row = _Row()
        row.owner = owner("Spouse")

        assert not held_by_member(row)
