"""No mode could apply a parser fix that corrects confident output.

`rebuild.yml` offered two modes, and between them they could not re-read a
filing the parser had scored well:

    fresh    -> `cli parse`                      -> filter(Disclosure.parsed == False)
    reparse  -> `cli parse --min-confidence 1.0` -> filter(parse_confidence < 1.0)

A filing scored 1.0 is `parsed`, so `fresh` skips it, and it is not below 1.0,
so `reparse` skips it too. Nothing could reach it. The mode's description said
"reparse = re-read filings already read, for a parser fix", which is the one
thing it does not do.

That is exactly the shape of the Schedule A/B defect: 40 House annual filings,
every one scored 1.0, with 95.5% of their stored "assets" being Schedule B
transactions and 91% of their real holdings missing. Merging the parser fix
would have changed nothing and no dispatch available in the UI could have
applied it -- the sort of gap where everyone believes the fix shipped.

`--reparse` on the CLI has always applied no filter at all. It simply had no
route through the workflow.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "rebuild.yml"


def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def parse_step() -> str:
    for job in workflow()["jobs"].values():
        for step in job.get("steps", []):
            if "src.cli parse" in (step.get("run") or ""):
                return step["run"]
    raise AssertionError("rebuild.yml has no parse step")


def modes() -> list[str]:
    # PyYAML reads the `on:` key as the boolean True.
    triggers = workflow().get("on") or workflow().get(True)
    return triggers["workflow_dispatch"]["inputs"]["mode"]["options"]


class TestAModeReachesEveryFiling:
    def test_the_choice_list_offers_one(self):
        assert "all" in modes()

    def test_it_runs_the_unfiltered_flag(self):
        """`--reparse` is the only selection that applies no filter."""
        run = parse_step()

        assert "--reparse" in run

    def test_it_does_not_also_pass_a_confidence_floor(self):
        """`--min-confidence` is checked BEFORE `reparse` in the query, so
        passing both would silently reinstate the filter this mode exists to
        avoid."""
        run = parse_step()
        branch = run.split('"all"')[1].split("elif")[0]

        assert "--reparse" in branch
        assert "--min-confidence" not in branch

    @pytest.mark.parametrize("mode", ["all", "reparse", "fresh"])
    def test_every_offered_mode_is_handled_by_the_step(self, mode):
        """A mode in the dropdown that the script does not branch on falls
        through to the `else`, and quietly does something other than its name."""
        run = parse_step()

        assert mode == "fresh" or f'"{mode}"' in run


class TestTheOtherModesAreUnchanged:
    def test_reparse_still_means_the_low_confidence_set(self):
        run = parse_step()

        assert "--min-confidence 1.0" in run

    def test_fresh_still_passes_no_selection_flag(self):
        run = parse_step()
        tail = run.split("else")[-1]

        assert "--reparse" not in tail
        assert "--min-confidence" not in tail


class TestTheDescriptionSaysWhatEachModeDoes:
    def test_it_no_longer_claims_reparse_reads_everything(self):
        triggers = workflow().get("on") or workflow().get(True)
        description = triggers["workflow_dispatch"]["inputs"]["mode"]["description"]

        assert "below 1.0" in description, (
            "the description must say reparse is limited to badly-scored "
            "filings, because that limit is what made a whole class of parser "
            "fix unapplicable"
        )
        assert "EVERY" in description


class TestTheQueryItselfAgrees:
    """The workflow is only half of it; these pin the behaviour the flags buy."""

    def _selected(self, db, **kwargs):
        from src.ingestion.orchestrator import IngestionOrchestrator

        orch = IngestionOrchestrator.__new__(IngestionOrchestrator)
        return orch._disclosures_to_parse(db, **kwargs)

    def test_a_confident_filing_is_reached_only_by_reparse(self, db_session):
        from datetime import datetime

        from src.db.models import Chamber, Disclosure, Member, Party

        member = Member(
            bioguide_id="RP00001",
            first_name="Re",
            last_name="Parse",
            chamber=Chamber.HOUSE,
            party=Party.DEMOCRAT,
            state="CA",
        )
        db_session.add(member)
        db_session.commit()

        confident = Disclosure(
            member_id=member.id,
            filing_year=2025,
            filing_type="O",
            filing_date=datetime(2025, 6, 1),
            document_id="RP-1",
            document_url="https://disclosures-clerk.house.gov/x.pdf",
            parsed=True,
            parse_confidence=1.0,
            has_text_layer=True,
        )
        db_session.add(confident)
        db_session.commit()

        assert confident.id not in {d.id for d in self._selected(db_session)}
        assert confident.id not in {d.id for d in self._selected(db_session, min_confidence=1.0)}
        assert confident.id in {d.id for d in self._selected(db_session, reparse=True)}
