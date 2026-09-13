"""Every field the pages read must be a field the API serves.

The Parsed Documents page shipped three columns -- Assets, Transactions,
Liabilities -- and three sort controls over them, all bound to
`doc.asset_count`, `doc.transaction_count` and `doc.liability_count`. None of
those fields was in the response. Every row read 0, 0, 0 and the sorts did
nothing, on the one page whose entire subject is what was extracted, and
nothing anywhere failed.

A missing field in a JavaScript template is silent by construction: it is
`undefined`, `|| 0` turns it into a zero, and the page renders a confident
answer that was never computed. This test is the thing that makes it loud.

It is deliberately narrow. It checks the Alpine loop variables the pages bind
API objects to, against the response models FastAPI actually publishes in its
OpenAPI schema -- not every identifier in every script.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.api.main import app

# Anchored to this file, not the working directory. Built with a bare
# `Path("src/templates")` these globs came up empty when pytest ran from
# anywhere but the repo root, the parametrised cases vanished, and pytest
# reported them SKIPPED rather than failing -- the same silent-pass failure
# these tests exist to catch.
REPO = Path(__file__).resolve().parents[1]
TEMPLATES = REPO / "src" / "templates"

# Loop variable -> the response model its object comes from. Anything not
# listed is not checked, which is the point: this is a guard on the bindings
# that reach the API, not a JavaScript linter.
BOUND_TO = {
    "anomaly": "AnomalyResponse",
    "doc": "DisclosureResponse",
    "trade": "DisclosureResponse",
    "member": "MemberResponse",
    "d": "DetectorTypeResponse",
}

# Names that are these loop variables in one place and an ordinary local in
# another. `d` is the detector in the anomalies legend and a `new Date` in two
# other files; `member` is a response object on the members page and a form
# field elsewhere.
LOCALS = {
    "total",  # pagination counts on the wrapper object, not the item
    "parse_confidence",  # read off `d` in parsed.html, where `d` is a document
}

REFERENCE = re.compile(r"\b(" + "|".join(BOUND_TO) + r")\.([a-z_][a-z0-9_]*)\b")


def _served() -> dict[str, set[str]]:
    schemas = app.openapi()["components"]["schemas"]
    return {name: set(body.get("properties", {})) for name, body in schemas.items()}


PAGES = sorted(TEMPLATES.glob("*.html"))

assert PAGES, f"no templates found under {TEMPLATES}; this file would test nothing"


@pytest.mark.parametrize("template", PAGES, ids=lambda p: p.name)
def test_no_page_reads_a_field_the_api_does_not_serve(template):
    served = _served()
    body = re.sub(r"<!--.*?-->", "", template.read_text(), flags=re.S)

    missing = []
    for variable, field in set(REFERENCE.findall(body)):
        if field in LOCALS:
            continue
        model = BOUND_TO[variable]
        if field not in served.get(model, set()):
            missing.append(f"{variable}.{field} (not in {model})")

    assert not missing, (
        f"{template.name} reads fields the API does not return: {sorted(missing)}. "
        "In a template that renders as undefined, and `|| 0` turns it into a "
        "confident zero."
    )
