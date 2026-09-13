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
