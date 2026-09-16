"""A purge command that no workflow runs is a fix that never shipped.

`purge-stale-wording` was written, tested and merged to delete findings whose
sentences the corrected detectors can no longer produce. It was never wired
into a pipeline. `purge-non-awards`, written for the same reason, was wired
into both -- so nothing about the omission looked odd in review.

Measured live, three full pipeline runs later: **425 findings** were still
being served with text no detector in the codebase can write.

    397  "High trading activity: 10-15 trades" and the other band labels
     27  naming `perfect_timing` -- a DISABLED detector -- over "combined
         score: 4/10", a maximum that does not exist
      1  "4 members saled NVDA within 1 days"

And it is worse than staleness. `anomaly_key` identifies a member-level
finding by its TITLE, so a corrected title is a NEW identity: the fixed
finding is INSERTED BESIDE the broken one rather than replacing it. **376
member-month pairs are published twice**, under the same person's name:

    Alan Armstrong — March 2026
      [1846] High trading activity: more than 100 trades in March 2026
      [5480] High trading activity: 701 trades in March 2026

That outcome was written down in `cmd_purge_stale_wording`'s own docstring
before any of it happened. Predicting a failure is not preventing it; running
the command is.

This test is deliberately about WIRING, not about the command -- the command
already has its own tests, and they all passed while it never ran.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
PIPELINES = ("rebuild.yml", "daily-update.yml")

# Every remediation command: one that DELETES published rows the current code
# would not write. Each must run in both pipelines.
PURGES = ("purge-non-awards", "purge-stale-wording", "purge-disabled")


def steps(name: str) -> list[dict]:
    workflow = yaml.safe_load((WORKFLOWS / name).read_text())
    return [s for job in workflow["jobs"].values() for s in job.get("steps", [])]


def index_of(name: str, command: str) -> int:
    for position, step in enumerate(steps(name)):
        if f"src.cli {command}" in (step.get("run") or ""):
            return position
    return -1


@pytest.mark.parametrize("workflow", PIPELINES)
@pytest.mark.parametrize("purge", PURGES)
class TestEveryPurgeRunsInEveryPipeline:
    def test_the_pipeline_runs_it(self, workflow, purge):
        assert index_of(workflow, purge) >= 0, (
            f"{workflow} never runs `{purge}`, so the rows it exists to delete "
            "are served for ever however well the command is tested"
        )

    def test_it_runs_before_the_analysis(self, workflow, purge):
        """Deleting first lets the same run re-derive each finding with the
        corrected wording. Deleting after would leave a one-run hole."""
        analyze = max(index_of(workflow, "analyze"), index_of(workflow, "cli analyze"))

        assert analyze >= 0
        assert index_of(workflow, purge) < analyze


class TestTheTwoPipelinesAgree:
    def test_neither_runs_a_purge_the_other_skips(self):
        """The omission was invisible precisely because one pipeline is not
        checked against the other."""
        ran = {w: {p for p in PURGES if index_of(w, p) >= 0} for w in PIPELINES}

        assert ran["rebuild.yml"] == ran["daily-update.yml"], ran


class TestTheCatalogueOfPurgesIsComplete:
    def test_every_purge_command_the_cli_defines_is_listed_here(self):
        """A future purge added to the CLI and not to PURGES would slip through
        this test the same way `purge-stale-wording` slipped through review."""
        import src.cli as cli

        defined = {
            name.replace("cmd_", "").replace("_", "-")
            for name in dir(cli)
            if name.startswith("cmd_purge_")
        }

        assert defined == set(PURGES), (
            f"the CLI defines {sorted(defined)} but this test guards {sorted(PURGES)}; "
            "an unguarded purge is one nothing makes run"
        )


class TestThePurgesCanBeRunOutOfBand:
    """A purge is useless at the moment you need it if the only way to run it is
    a three-and-a-half hour pipeline.

    That is not hypothetical. Rebuild #15 finished carrying the corrected
    `trade_clustering` detector but not the purge entry for the wording it
    replaced, so 169 findings were published *beside* their own corrections --
    Thom Tillis served at once as "7 in a row over 0 days" and "14 in a row
    within a short period", under his own name. The fix was merged. Running it
    meant re-ingesting, re-parsing 400 filings and re-analysing, which would
    have moved the whole corpus as a side effect.

    `maintenance.yml` exists so a remediation command can be dispatched on its
    own. These tests guard the two things that make it safe rather than the
    thing that makes it convenient.
    """

    MAINTENANCE = "maintenance.yml"

    def workflow(self) -> dict:
        return yaml.safe_load((WORKFLOWS / self.MAINTENANCE).read_text())

    def test_it_offers_every_purge_the_cli_defines(self):
        """The pipelines are checked against each other above. This one has no
        counterpart, so it is checked against the CLI."""
        on = self.workflow().get(True) or self.workflow().get("on")
        offered = set(on["workflow_dispatch"]["inputs"]["command"]["options"])

        assert offered == set(PURGES), (
            f"maintenance.yml offers {sorted(offered)} but the CLI defines "
            f"{sorted(PURGES)}; a purge it cannot run is one that needs a pipeline"
        )

    def test_it_waits_for_the_other_writers(self):
        """Deleting rows while `analyze` inserts them is the same hazard the
        other two pipelines already serialise against, so it shares their group.
        Without this it could race the 06:00 cron."""
        concurrency = self.workflow()["concurrency"]
        others = [yaml.safe_load((WORKFLOWS / p).read_text())["concurrency"] for p in PIPELINES]

        assert all(concurrency["group"] == o["group"] for o in others), concurrency
        assert concurrency["cancel-in-progress"] is False

    def test_it_defaults_to_a_dry_run(self):
        """It deletes published rows about named people. The safe setting is the
        one you get by pressing the button without reading."""
        on = self.workflow().get(True) or self.workflow().get("on")

        assert on["workflow_dispatch"]["inputs"]["dry_run"]["default"] is True

    def test_it_does_not_analyse(self):
        """Re-deriving the findings is the nightly's job. Doing it here would
        make a three-second delete move the whole corpus, which is the side
        effect this workflow exists to avoid."""
        runs = " ".join(s.get("run") or "" for s in steps(self.MAINTENANCE))

        assert "cli analyze" not in runs
