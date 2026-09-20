"""A code in square brackets is the asset type, and it was read as a company.

Two rows of the corpus recorded trades against a company called "VA":

    Prudential RILA - 10% Buffer 3-year S&P 500 Index [VA]

`[VA]` is the House form's abbreviation for a variable annuity. It was read as
a symbol because `ASSET_CLASS_CODES` -- a copy, kept in this repository, of a
list the Clerk maintains -- had never heard of it. The form itself says where
the list lives:

    * For the complete list of asset type abbreviations, please visit
      https://fd.house.gov/reference/asset-type-codes.aspx.

A local copy of a list published elsewhere drifts, and this one had.

The second hole was quieter. The bracket loop rejected a code it *did* know,
and then the keyword pass underneath went looking for capitals in the same
untouched string and found the very same code:

    OnSolve LLC Shares [PS]                              -> "PS"
    Ternium S.A. ... Depositary Shares (TX) [ST]         -> "ST"
    IShares Core S&P Small Cap E [OT]                    -> "OT"

The Ternium row is the one that shows what was at stake: its real symbol is
printed on the line, in parentheses, and the row was filed under "ST" instead.

So the tag is removed from the text before anything reads it, and the rule is
the shape the form prints rather than a list of values. All 5,102 bracketed
codes in the corpus are two characters and every one is an asset class; not one
is a symbol. Parentheses are left alone, because (BA), (GS), (PM) and (IR) are
Boeing, Goldman Sachs, Philip Morris and Ingersoll Rand -- real symbols that
collide with asset-class codes, and the bracket is the only thing telling them
apart.
"""

from __future__ import annotations

import textwrap

from src.parsing.ptr_parser import ASSET_CLASS_TAG, PTRParser


def _ticker(text: str) -> str | None:
    return PTRParser.__new__(PTRParser)._extract_ticker(text)


# Verbatim from the corpus. Each one was filed under the bracketed code.
def test_an_asset_class_the_repository_never_heard_of():
    assert _ticker("Prudential RILA - 10% Buffer 3-year\nS&P 500 Index [VA]") is None


def test_the_keyword_pass_cannot_re_harvest_a_rejected_code():
    # "Shares" reaches the keyword pass; "PS" is already in ASSET_CLASS_CODES,
    # so the bracket loop had rejected it and the keyword pass took it anyway.
    assert _ticker("OnSolve LLC Shares [PS]") is None
    assert _ticker("IShares Core S&P Small Cap E [OT]") is None


def test_a_tag_is_not_preferred_over_a_symbol_printed_beside_it():
    row = "Ternium S.A. Ternium S.A. American\nDepositary Shares (TX) [ST]"
    # TX is in NON_TICKERS (it reads as the state), so this row has no symbol
    # this parser will assert. What it must never do is answer "ST".
    assert _ticker(row) != "ST"


def test_a_parenthesised_code_is_still_a_symbol():
    # Every one of these is both a real ticker and an asset-class code.
    assert _ticker("Boeing Company (BA) [ST]") == "BA"
    assert _ticker("Goldman Sachs Group, Inc. (GS) [ST]") == "GS"
    assert _ticker("Philip Morris International Inc\nCommon Stock (PM) [ST]") == "PM"
    assert _ticker("Ingersoll Rand Inc. (IR) [ST]") == "IR"


def test_a_bracketed_symbol_wider_than_a_tag_still_reads():
    # The tag is two characters. A four-letter symbol in brackets is not a tag.
    assert _ticker("Microsoft Corporation [MSFT]") == "MSFT"


def test_the_rule_is_the_shape_not_a_list_of_values():
    # A two-character bracketed code the list has never seen is still removed.
    assert ASSET_CLASS_TAG.search("Something [QZ]")
    assert _ticker("Some Fund Shares [QZ]") is None


def test_every_bracketed_code_the_corpus_prints_is_two_characters():
    # The grounding for deciding by width: 5,102 occurrences, 13 distinct codes,
    # all two characters, all asset classes. Recorded here so a future reader
    # can see what the rule was measured against rather than taking it on trust.
    seen = {
        "ST": 4371,
        "GS": 383,
        "OT": 137,
        "CS": 57,
        "CT": 51,
        "OP": 28,
        "HN": 24,
        "OI": 22,
        "PS": 16,
        "AB": 5,
        "OL": 4,
        "ET": 2,
        "VA": 2,
    }
    assert sum(seen.values()) == 5102
    assert all(len(code) == 2 for code in seen)
    for code in seen:
        assert ASSET_CLASS_TAG.fullmatch(f"[{code}]")


def test_the_tag_is_removed_before_any_branch_runs():
    """Structural, because "the bracket loop skips it" is what used to be true.

    The old code rejected a known code inside one branch and left it in the
    string, which is how the keyword pass underneath got hold of it again. The
    fix is only a fix if the removal happens before any branch reads the text,
    so this reads the real function rather than re-testing a value.
    """
    import ast
    import inspect

    tree = ast.parse(textwrap.dedent(inspect.getsource(PTRParser._extract_ticker)))
    body = tree.body[0].body
    statements = [
        node
        for node in body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]

    def assigns_text_from_tag(node):
        return (
            isinstance(node, ast.Assign)
            and any(getattr(t, "id", None) == "text" for t in node.targets)
            and "ASSET_CLASS_TAG" in ast.dump(node.value)
        )

    strips = [i for i, node in enumerate(statements) if assigns_text_from_tag(node)]
    assert strips, "the asset-class tag is no longer removed from the text"

    reads_text = [
        i
        for i, node in enumerate(statements)
        if i not in strips and "text" in {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
    ]
    # The empty-text guard is allowed to come first; nothing that looks for a
    # symbol may.
    assert min(reads_text) < strips[0], "expected the `if not text` guard first"
    assert [i for i in reads_text if i > min(reads_text)][0] > strips[0]
