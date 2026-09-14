"""Both pipeline workflows write to the same production database.

Nothing kept them apart. Measured on 2026-09-14: the 11:18 `daily-update` cron
overlapped `rebuild` runs 12 and 13 in their entirety -- six hours of two
workflows ingesting and analysing concurrently against one database -- and both
run `cli analyze` against the same `anomalies` table.

One cost was already paid. Findings appeared on the live site that had to be
traced by timestamp across four job logs to attribute: rebuild 13's parse
created the Senate transactions, and the cron's analysis step, which started
thirty-six minutes later, wrote the findings. Nothing was wrong with the data;
nobody could say where it came from.

The other has not been paid yet and is worse. `persist_anomalies` deduplicates
with a SELECT followed by an INSERT, so two analysis passes interleaving can
both see "not present" and both insert.

A shared `concurrency.group` is the whole fix, and the group name matching
across the two files is the entire mechanism -- which is exactly the kind of
thing that gets broken by a rename six months from now, so it is asserted rather
than assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
PIPELINES = ("rebuild.yml", "daily-update.yml")


def _workflow(name: str) -> dict:
    path = WORKFLOWS / name
    assert path.exists(), f"missing workflow {path}"
    return yaml.safe_load(path.read_text())


@pytest.mark.parametrize("name", PIPELINES)
def test_the_workflow_declares_a_concurrency_group(name):
    concurrency = _workflow(name).get("concurrency")

    assert concurrency, (
        f"{name} writes to the production database and declares no concurrency "
        "group, so it can run alongside the other pipeline workflow"
    )
    assert concurrency.get("group")


def test_both_pipelines_share_the_same_group():
    groups = {name: _workflow(name)["concurrency"]["group"] for name in PIPELINES}

    assert len(set(groups.values())) == 1, (
        f"the two workflows are in different concurrency groups, so neither "
        f"waits for the other: {groups}"
    )


@pytest.mark.parametrize("name", PIPELINES)
def test_a_run_in_progress_is_never_cancelled(name):
    """A nightly ingest halfway through must not be killed by a manual
    dispatch. Queueing is the point; cancelling would lose the work."""
    assert _workflow(name)["concurrency"].get("cancel-in-progress") is False


def _step_commands(name: str) -> list[str]:
    """Every `run:` line in the workflow's single job, in order."""
    job = next(iter(_workflow(name)["jobs"].values()))
    return [step.get("run", "") for step in job["steps"]]


@pytest.mark.parametrize("name", PIPELINES)
def test_the_purge_runs_before_the_analysis(name):
    """Ordering that nothing else enforces, on a finding attached to a person.

    `purge-non-awards` deletes contract front-run findings that no award in the
    table supports -- the ones published while a deobligation counted as an
    award. `analyze` then re-derives whatever is still justified. Run the other
    way round and the purge deletes findings the analysis has just legitimately
    rewritten, leaving the site short until the next run; drop the purge and the
    bad findings are served indefinitely, because `persist_anomalies` only ever
    inserts.
    """
    commands = _step_commands(name)
    purge = [i for i, c in enumerate(commands) if "purge-non-awards" in c]
    analyze = [i for i, c in enumerate(commands) if "cli analyze" in c]

    assert purge, f"{name} never runs `cli purge-non-awards`"
    assert analyze, f"{name} never runs `cli analyze`"
    assert max(purge) < min(analyze), (
        f"{name} purges contract findings after analysing, which deletes the "
        f"findings that pass just produced"
    )


# GitHub terminates a job at 360 minutes. That is a hard kill of the runner, not
# a cancellation, so nothing runs afterwards -- an `if: always()` step included.
GITHUB_HARD_KILL_MINUTES = 360


@pytest.mark.parametrize("name", PIPELINES)
def test_the_workflow_stops_before_github_kills_it(name):
    """Both pipelines end with a summary step that must be allowed to run.

    Measured on 2026-09-14: `daily-update` run 224 started at 11:18 and ended at
    17:20 marked "cancelled" -- the 360-minute wall, with no `timeout-minutes` of
    its own. Six hours of ingest, and the "Feeds that FAILED despite the green
    tick" block never printed, because a hard kill takes the runner with it and
    `if: always()` has nothing to run on.

    A job timeout below the wall is a cancellation instead, and `always()` does
    survive a cancellation. So the last thing a truncated run does is still say
    how far it got, which for an unwatched 06:00 cron is the whole point.
    """
    job = next(iter(_workflow(name)["jobs"].values()))
    timeout = job.get("timeout-minutes")

    assert timeout is not None, (
        f"{name} declares no timeout-minutes, so GitHub hard-kills it at "
        f"{GITHUB_HARD_KILL_MINUTES} and its summary step never runs"
    )
    assert timeout < GITHUB_HARD_KILL_MINUTES, (
        f"{name} times out at {timeout}, at or past GitHub's own "
        f"{GITHUB_HARD_KILL_MINUTES}-minute kill, which defeats the point"
    )


@pytest.mark.parametrize("name", PIPELINES)
def test_the_summary_step_runs_even_when_something_failed(name):
    """The timeout above only helps if there is a step for it to protect."""
    job = next(iter(_workflow(name)["jobs"].values()))
    always = [s for s in job["steps"] if str(s.get("if", "")).strip() == "always()"]

    assert always, f"{name} has no `if: always()` step, so a failed run reports nothing"
