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

# What each remediation command's flag MEANS, which is the thing that must never
# be guessed. `previews` is the flag that makes it report instead of write;
# `writes` is the flag that makes it write. Exactly one is set, and the two
# conventions are opposite.
#
# `cli` names the command argparse actually sees, where the workflow's label is
# not itself a CLI command. `extra` lists the other flags the workflow sends,
# which must be accepted too -- argparse aborts the run on any it does not know,
# and a re-parse that aborts leaves the corpus exactly as wrong as it was.
CONVENTIONS = {
    "purge-stale-wording": {"previews": "--dry-run"},
    "purge-disabled": {"previews": "--dry-run"},
    "purge-non-awards": {"previews": "--dry-run"},
    "repair-house-attribution": {"writes": "--apply", "extra": ("--years",)},
    "move-filing": {"writes": "--apply", "extra": ("--document-id", "--to-bioguide")},
    "reparse-annuals": {
        "previews": "--dry-run",
        "cli": "parse",
        "extra": ("--reparse", "--annual-only", "--limit"),
    },
}


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
        counterpart, so it is checked against the CLI.

        A subset rather than an equality: the workflow also carries remediation
        commands that are not purges -- `repair-house-attribution` moves filings
        rather than deleting findings -- and those are covered by the flag test
        below."""
        on = self.workflow().get(True) or self.workflow().get("on")
        offered = set(on["workflow_dispatch"]["inputs"]["command"]["options"])

        assert set(PURGES) <= offered, (
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


class TestTheWorkflowPassesTheRightFlag:
    """The two dry-run conventions are OPPOSITE, and guessing writes to production.

        the purges                apply by default; ``--dry-run`` only previews
        repair-house-attribution  previews by default; ``--apply`` writes

    The first version of `maintenance.yml` asserted that only
    `purge-stale-wording` took `--dry-run`, warned that the other two "have no
    --dry-run", and ran them for real. All three declare it. So a dry run of
    `purge-disabled` or `purge-non-awards` would have deleted rows while
    printing that it was only previewing -- the safe default defeated for two
    of the three commands it existed to protect.

    Every test above passed the whole time, because they checked that the input
    existed and defaulted to true, not that anything HONOURED it.

    So this asks the real parser what each command accepts and checks the
    workflow against the answer. Through `--help` rather than by importing: the
    parser is built inside `main()` and cannot be reached otherwise, and going
    through the CLI is what the workflow does too.
    """

    MAINTENANCE = "maintenance.yml"

    @staticmethod
    def offered() -> list[str]:
        workflow = yaml.safe_load((WORKFLOWS / "maintenance.yml").read_text())
        on = workflow.get(True) or workflow.get("on")
        return on["workflow_dispatch"]["inputs"]["command"]["options"]

    @classmethod
    def branch_of(cls, command: str) -> str:
        """The `case` arm this command runs, alternation labels included.

        Three of the commands share one arm -- `a|b|c)` -- so splitting on
        `command)` finds nothing for two of them and silently returns the whole
        script, which would make every assertion below pass for the wrong
        reason."""
        run = next(
            s["run"] for s in steps(cls.MAINTENANCE) if "inputs.command" in (s.get("run") or "")
        )
        arms = run.split(";;")
        for arm in arms:
            label = arm.split(")", 1)[0]
            if command in [part.strip() for part in label.strip().splitlines()[-1].split("|")]:
                return arm.split(")", 1)[1]
        raise AssertionError(f"maintenance.yml has no case arm for `{command}`")

    @classmethod
    def flags_sent(cls, command: str) -> set[str]:
        """Every `--flag` the workflow's arm for this command passes on.

        Read out of the workflow rather than listed, because a list passes while
        the workflow sends something else -- the same failure as asserting a
        command's flags instead of asking it."""
        import re

        return {
            flag
            for line in cls.branch_of(command).splitlines()
            if "FLAGS=" in line
            for flag in re.findall(r"--[a-z0-9][a-z0-9-]*", line)
        }

    @staticmethod
    def accepted_flags(command: str) -> set[str]:
        """What argparse actually accepts, asked rather than assumed."""
        import subprocess
        import sys

        command = CONVENTIONS.get(command, {}).get("cli", command)
        result = subprocess.run(
            [sys.executable, "-m", "src.cli", command, "--help"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        assert result.returncode == 0, result.stderr
        return {word.strip(" ,") for word in result.stdout.split() if word.startswith("--")}

    def test_every_offered_command_has_a_stated_convention(self):
        """An unlisted command is one whose flag the workflow would have to
        guess, and guessing is what caused the bug."""
        assert set(self.offered()) == set(CONVENTIONS), (
            f"maintenance.yml offers {sorted(self.offered())} but conventions are "
            f"declared for {sorted(CONVENTIONS)}"
        )

    @pytest.mark.parametrize("command", sorted(CONVENTIONS))
    def test_the_cli_really_accepts_the_flag_the_workflow_sends(self, command):
        """The exact check that was missing. `purge-disabled` and
        `purge-non-awards` were assumed not to take `--dry-run`; they do."""
        convention = CONVENTIONS[command]
        flag = convention.get("previews") or convention["writes"]

        assert flag in self.accepted_flags(command), (
            f"maintenance.yml sends `{flag}` to `{command}`, which does not accept it. "
            "argparse would abort the run."
        )

    @pytest.mark.parametrize("command", sorted(CONVENTIONS))
    def test_the_cli_accepts_every_other_flag_the_workflow_sends(self, command):
        """The preview flag is not the only one that can be wrong.

        `reparse-annuals` sends `--reparse --annual-only --limit`, none of which
        the dry-run test above would notice going missing. argparse aborts on an
        unknown flag, so a renamed option turns a re-parse campaign into a
        workflow that fails at the last step every time it is dispatched.

        The flags are read out of the workflow rather than listed here. A list
        would pass while the workflow sent something else entirely, which is the
        same failure mode as asserting what a command's flags are instead of
        asking it."""
        accepted = self.accepted_flags(command)
        for flag in sorted(self.flags_sent(command)):
            assert flag in accepted, (
                f"maintenance.yml sends `{flag}` to `{command}`, which does not accept it. "
                "argparse would abort the run."
            )

    @pytest.mark.parametrize("command", sorted(CONVENTIONS))
    def test_the_declared_flags_are_the_ones_actually_sent(self, command):
        """`extra` is documentation, and documentation that drifts is worse than
        none: it is what says, in review, that the workflow is fine."""
        declared = set(CONVENTIONS[command].get("extra", ()))
        convention = CONVENTIONS[command]
        sent = self.flags_sent(command) - {convention.get("previews"), convention.get("writes")}

        assert sent == declared, (
            f"maintenance.yml sends {sorted(sent)} to `{command}` but CONVENTIONS declares "
            f"{sorted(declared)}"
        )

    @pytest.mark.parametrize("command", sorted(CONVENTIONS))
    def test_the_workflow_sends_that_flag_on_the_right_side_of_the_toggle(self, command):
        """A preview flag must sit under `dry_run == true`; a write flag must
        sit under its negation. Swapping them is silent and destructive."""
        branch = self.branch_of(command)
        convention = CONVENTIONS[command]

        if "previews" in convention:
            assert convention["previews"] in branch, (
                f"{command} previews with `{convention['previews']}` and the workflow never sends it, "
                "so a dry run writes to production"
            )
            assert 'dry_run }}" = "true"' in branch, (
                f"{command} sends its preview flag, but not under `dry_run == true`"
            )
        else:
            assert convention["writes"] in branch
            assert 'dry_run }}" != "true"' in branch, (
                f"{command} writes with `{convention['writes']}`, which must be gated on "
                "dry_run being OFF, not on it being ON"
            )
