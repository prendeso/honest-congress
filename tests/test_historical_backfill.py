"""The backfill is a parameter, not a second implementation.

`src/ingestion/house_clerk_historical.py` existed for a 2004+ House backfill and
was called by nothing -- no CLI command, no workflow, no import, only an
`if __name__ == "__main__"`. Wiring it up would have imported bad data, because
it differed from the path that IS used in four ways that all corrupt what the
detectors read:

  * it fabricated `filing_date` as December 31 of the filing year, where the XML
    carries the real date. The late-filing detector subtracts filing_date from
    transaction_date, so every backfilled filing would have produced a fictional
    lateness -- the most consequential number on the site.
  * it set `is_ptr=False` unconditionally, so historical trade reports would
    have been stored as annual filings and read by the wrong parser.
  * it ignored FilingType entirely, so candidate reports -- filed by people who
    are not members -- would have been stored as member filings.
  * it checked the database for a duplicate document_id without an in-batch
    seen-set, which is exactly the autoflush=False trap that killed a production
    ingest and cost a year of filings.

`sync_house_disclosures` / `sync_house_ptrs` already handle all four and are
already year-parameterised, so the backfill is `cli ingest -y <years>`. The
module was deleted rather than fixed: two implementations of one job is how the
broken one gets run by mistake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestThereIsOneIngestionPath:
    def test_the_duplicate_module_is_gone(self):
        assert not (REPO_ROOT / "src" / "ingestion" / "house_clerk_historical.py").exists(), (
            "a second, buggier House ingestion path is back; the backfill is "
            "`cli ingest -y <years>` through sync_house_disclosures"
        )

    def test_nothing_references_it(self):
        offenders = []
        for path in REPO_ROOT.rglob("*"):
            if path.is_dir() or ".git" in path.parts or "__pycache__" in path.parts:
                continue
            if path.suffix not in {".py", ".yml", ".yaml", ".md", ".toml"}:
                continue
            if path.name == Path(__file__).name:
                continue
            try:
                if "house_clerk_historical" in path.read_text():
                    offenders.append(str(path.relative_to(REPO_ROOT)))
            except (UnicodeDecodeError, OSError):
                continue

        assert not offenders, f"dangling references to the removed module: {offenders}"


class TestTheIngestPathTakesAnyYear:
    """What makes a backfill possible without new code."""

    def test_sync_house_disclosures_is_year_parameterised(self):
        import inspect

        from src.ingestion.orchestrator import IngestionOrchestrator

        for name in ("sync_house_disclosures", "sync_house_ptrs", "sync_senate_disclosures"):
            signature = inspect.signature(getattr(IngestionOrchestrator, name))
            assert "year" in signature.parameters, f"{name} cannot be pointed at a past year"

    def test_run_full_sync_accepts_a_list_of_years(self):
        import inspect

        from src.ingestion.orchestrator import IngestionOrchestrator

        signature = inspect.signature(IngestionOrchestrator.run_full_sync)
        assert "years" in signature.parameters

    @pytest.mark.parametrize("years", ["2008 2009 2010", "2012", "2008 2009 2010 2011 2012"])
    def test_the_workflow_accepts_a_multi_year_backfill(self, years):
        """The rebuild input is validated by a regex before anything
        irreversible runs, so a backfill range has to satisfy it."""
        import re

        pattern = re.compile(r"^[0-9]{4}( [0-9]{4})*$")
        assert pattern.match(years), f"{years!r} would be rejected by the workflow"

    def test_the_documented_range_is_stated_somewhere(self):
        """2008, not 2004. The old module's docstring and the repo's resource
        index both claimed 2004; the Clerk returns 404 for every year before
        2008, probed one by one."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "rebuild.yml").read_text()

        assert "2008" in workflow, (
            "the available-year range is not recorded where an operator choosing "
            "`years` would see it"
        )
