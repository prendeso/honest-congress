"""Every inline script in a template must actually parse.

The pages carry real logic in `<script>` blocks, and none of it is compiled,
bundled or linted by anything. A stray bracket or a botched regex escape is
invisible here and fatal in the browser: Alpine's `x-init` never runs, the page
renders its empty state, and the only sign is a console error nobody reading
the site will see.

Node is used because it is the only JavaScript parser available, and the check
is skipped rather than failed where it is absent -- a missing parser is not a
broken template.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

TEMPLATES = sorted(Path("src/templates").rglob("*.html"))
SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is not available to parse the scripts"
)


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_inline_scripts_parse(template):
    blocks = SCRIPT.findall(template.read_text())

    for index, block in enumerate(blocks):
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
            handle.write(block)
            path = handle.name

        result = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        assert result.returncode == 0, (
            f"{template.name} script block {index} does not parse:\n{result.stderr}"
        )


def test_at_least_one_template_has_a_script():
    """Guard against the regex silently matching nothing and every test passing."""
    assert any(SCRIPT.search(t.read_text()) for t in TEMPLATES)


READ_STATUS_PAGES = ("trades.html", "parsed.html", "disclosures.html")


def _helper(source: str, name: str) -> str:
    """The body of one helper function from an inline script."""
    start = source.index(f"{name}(value) {{")
    depth = 0
    for offset, char in enumerate(source[start:], start):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return re.sub(r"\s+", " ", source[start : offset + 1])
    raise AssertionError(f"{name} has unbalanced braces")


@pytest.mark.parametrize("name", ["readLabel", "readClass"])
def test_the_three_filing_lists_agree_on_how_well_a_filing_was_read(name):
    """Trades, Parsed Documents and Disclosures list overlapping filings.

    Each keeps its own copy of these two helpers, because the pages have no
    build step and no shared script file to put them in. Copies drift, and a
    drifted copy here means the same filing is 79% on one page and amber-warned
    on another. This pins them to each other instead.
    """
    bodies = {
        page: _helper((Path("src/templates") / page).read_text(), name)
        for page in READ_STATUS_PAGES
    }

    distinct = set(bodies.values())
    assert len(distinct) == 1, (
        f"{name} differs between pages that list the same filings: "
        + "; ".join(f"{page}: {body}" for page, body in bodies.items())
    )
