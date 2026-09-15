"""The nightly workflow ingested filings every night and never parsed one.

`cli ingest` stores a `Disclosure` with `parsed=False`. `cli parse` is what
downloads its PDF and turns it into `Transaction` rows. `daily-update.yml` ran
the first and not the second, and only `rebuild.yml` -- a manual
`workflow_dispatch` -- ever ran `parse` at all.

So a PTR filed on Monday sat in the database unread until somebody happened to
dispatch a rebuild, and each night `cli analyze` ran over a trade table that
that night's own ingest had not added a single row to. The site's freshness
depended on a human remembering.

It stayed invisible because every surface agreed with itself: `cli stats`
counts disclosures and reports them rising, the job is green, and the anomaly
totals move a little every night from the other feeds. Nothing anywhere said
"these filings have never been read".

This is a property of the pipeline rather than of one file, so it is asserted
over both workflows: anything that ingests must parse before it analyses.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
PIPELINES = ("rebuild.yml", "daily-update.yml")


def steps(name: str) -> list[dict]:
    workflow = yaml.safe_load((WORKFLOWS / name).read_text())
    found: list[dict] = []
    for job in workflow["jobs"].values():
        found.extend(job.get("steps", []))
    return found


def index_of(name: str, command: str) -> int:
    """Where `python -m src.cli <command>` first appears in the step list."""
    for position, step in enumerate(steps(name)):
        run = step.get("run") or ""
        if f"src.cli {command}" in run:
            return position
    return -1


@pytest.mark.parametrize("name", PIPELINES)
class TestAWorkflowThatIngestsMustParse:
    def test_it_has_a_parse_step(self, name):
        assert index_of(name, "parse") >= 0, (
            f"{name} runs `ingest` and never `parse`, so the filings it stores "
            "are never turned into transactions and the analysis that follows "
            "runs over a trade table the run did not add to"
        )

    def test_parse_comes_after_ingest(self, name):
        ingest, parse = index_of(name, "ingest"), index_of(name, "parse")

        assert ingest >= 0 and parse > ingest

    def test_analyze_comes_after_parse(self, name):
        parse, analyze = index_of(name, "parse"), index_of(name, "analyze")

        assert analyze >= 0 and analyze > parse, (
            "analysing before parsing reads the previous run's transactions"
        )


class TestTheNightlyParseCannotFailSilently:
    """`continue-on-error` on a step with a `timeout-minutes` is exactly the
    combination that shows a green tick over a dead step -- which is why this
    workflow already carries a summary block naming the ones that failed. A new
    step with that pair must be in it."""

    def _parse_step(self) -> dict:
        for step in steps("daily-update.yml"):
            if "src.cli parse" in (step.get("run") or ""):
                return step
        raise AssertionError("daily-update.yml has no parse step")

    def test_it_is_bounded_in_time_rather_than_in_requests(self):
        """Each filing is a download, so the run is bounded by how slow the
        Clerk and eFD are that night, not by how many filings there are (#60)."""
        assert self._parse_step().get("timeout-minutes")

    def test_a_slow_night_still_reaches_the_analysis(self):
        """Every filing commits as it goes, so a timeout keeps its work and the
        rest of the pipeline should still run over it."""
        assert self._parse_step().get("continue-on-error") is True

    def test_its_outcome_is_named_in_the_summary(self):
        step_id = self._parse_step().get("id")
        assert step_id, "the step has no id, so its outcome cannot be reported"

        summary = "".join(
            step.get("run") or ""
            for step in steps("daily-update.yml")
            if "GITHUB_STEP_SUMMARY" in (step.get("run") or "")
        )

        assert f"steps.{step_id}.outcome" in summary, (
            "the step can fail or time out behind a green tick and nothing says so"
        )

    def test_the_work_is_bounded_so_a_backlog_drains_over_nights(self):
        assert "--limit" in self._parse_step()["run"]


class TestTheStepCeilingsFitTheJob:
    @pytest.mark.parametrize("name", PIPELINES)
    def test_the_steps_cannot_outlast_the_job(self, name):
        """A step ceiling above the job's own is not a ceiling. Summing them is
        the check that matters: the job timeout has to leave room for the steps
        that carry no ceiling of their own."""
        workflow = yaml.safe_load((WORKFLOWS / name).read_text())

        for job in workflow["jobs"].values():
            job_limit = job.get("timeout-minutes")
            if not job_limit:
                continue
            ceilings = [
                s["timeout-minutes"] for s in job.get("steps", []) if s.get("timeout-minutes")
            ]
            assert sum(ceilings) < job_limit, (
                f"{name}: step ceilings total {sum(ceilings)}m against a job "
                f"limit of {job_limit}m, leaving nothing for the uncapped steps"
            )
