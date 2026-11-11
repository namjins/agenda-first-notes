"""The metric on hand-built outputs rendered by the app's own renderer. SYNTHETIC names only."""

from __future__ import annotations

from typing import Any

import pytest
from agenda_first_notes import render
from agenda_first_notes.agenda import AgendaNode, parse_text

from prompt_tuning.app import BASELINE, PREFIX, SEED, check_template, template
from prompt_tuning.metric import Meeting, gold_warnings, parse_gold, reference_words, resolve, score

AGENDA = """Reimbursement guidelines
Activity coordination
Ward history
Sister Ilse Varga - Primary
  Updates
  Nothing
Brother Oren Castellanos - Elders Quorum
  Updates
Temple night
  Dates
  Drivers
"""
TREE = parse_text(AGENDA)


def node(path: str) -> str:
    n = resolve(TREE, path)[0]
    assert n is not None, path
    return n.id


def note(path: str, text: str, follow_up: bool = False) -> dict[str, Any]:
    ev = [{"start": 0, "end": 1, "quote": "x", "transcript_version_id": None}]
    return {"agenda_node_id": node(path), "text": text, "follow_up": follow_up, "owner": None, "category": "decision",
            "evidence": ev, "status": "verified", "origin": "model", "position": 0}  # fmt: skip


def md(*notes: dict[str, Any], tree: list[AgendaNode] = TREE) -> str:
    return render.render(title="T", meeting_date=None, tree=tree, notes=list(notes), coverage_complete=True)


GOLD = parse_gold(
    {
        "workspace": "ward_council",
        "expected": [
            {"item": "Reimbursement guidelines", "keywords": [["reimburs"], ["dec", "31"]], "follow_up": False},
            {"item": "Sister Ilse Varga - Primary > Updates", "keywords": [["nursery"]], "follow_up": True},
        ],
        "not_discussed": ["Ward history", "Temple night", "Dates", "Drivers"],
        "forbidden": [{"keywords": [["activity"], ["reimburs"]], "why": "the rule is not about activities"}],
    },
    "test",
)
GOOD = [
    note("Reimbursement guidelines", "Receipts must be reimbursed before December 31."),
    note("Sister Ilse Varga - Primary > Updates", "Nursery needs two more helpers.", follow_up=True),
]


def run(*notes: dict[str, Any], high: list[str] | None = None) -> tuple[float, str]:
    s, fb = score(md(*notes), TREE, GOLD, high or [], [])
    return s, "\n".join(fb)


def test_perfect_output_scores_one() -> None:
    s, fb = run(*GOOD)
    assert s == 1.0, fb


def test_misfiled_fact_is_named_with_both_items() -> None:
    s, fb = run(note("Activity coordination", "Activity receipts reimbursed before Dec 31."), GOOD[1])
    assert s < 0.5
    assert (
        "Expected 'reimburs' + 'dec' under 'Reimbursement guidelines' but it appeared under 'Activity coordination'"
        in fb
    )
    assert "False 'Not discussed.' for 'Reimbursement guidelines'" in fb
    assert "Wrong note under 'Activity coordination'" in fb  # forbidden


def test_false_absence_on_group_no_discussion() -> None:
    s, fb = run(GOOD[0])
    assert "False 'Not discussed.' for 'Sister Ilse Varga - Primary > Updates'" in fb  # group: "No discussion."
    assert "Missing: no note about 'nursery'" in fb
    assert s == pytest.approx(0.5 / 1.15)


def test_note_under_not_discussed_item() -> None:
    s, fb = run(*GOOD, note("Ward history", "History binder is complete."))
    assert s == pytest.approx(1 / 1.1)
    assert "'Ward history' was not discussed, but got: 'History binder is complete.'" in fb


def test_follow_up_tag_checked_both_ways() -> None:
    s, fb = run(note("Reimbursement guidelines", "Reimburse by Dec 31.", follow_up=True), GOOD[1])
    assert s == pytest.approx(1 / 1.05) and "should not be tagged [Follow-up]" in fb
    s, fb = run(GOOD[0], note("Sister Ilse Varga - Primary > Updates", "Nursery helpers needed."))
    assert s == pytest.approx(1 / 1.05) and "needs a [Follow-up] tag" in fb


def test_empty_output_scores_zero() -> None:
    s, fb = run()
    assert s == 0.0 and "No notes were rendered" in fb


def test_high_findings_and_retelling_are_penalised() -> None:
    s, fb = run(
        *GOOD,
        note("Activity coordination", "A participant mentioned the calendar."),
        high=["HIGH name finding: X"] * 10,
    )
    assert s == pytest.approx(1 / 1.32)  # high findings capped at 0.30
    assert "HIGH name finding: X" in fb and "Retells the conversation" in fb


def test_mismatched_meeting_with_only_absences() -> None:
    gold = parse_gold({"workspace": "bishopric", "expected": [], "not_discussed": ["Ward history"]}, "t")
    assert score(md(), TREE, gold, [], [])[0] == 1.0
    assert score(md(note("Ward history", "Discussion occurred.")), TREE, gold, [], [])[0] == pytest.approx(1 / 1.1)


def test_heading_folding_and_warnings() -> None:
    tree = parse_text("Quarterly report coming up.  Make sure it’s done\nUpdates\nA\n  Updates\n")
    assert resolve(tree, "quarterly REPORT coming up. make sure it's done")[0] is tree[0]
    assert resolve(tree, "A > Updates")[0] is tree[2].children[0]
    assert "matches 2 agenda items" in (resolve(tree, "Updates")[1] or "")
    gold = parse_gold(
        {"workspace": "ward_council", "expected": [{"item": "Nope", "keywords": [["x"]], "follow_up": None}]}, "t"
    )
    warns = gold_warnings(Meeting("m", "", "", gold), tree)
    assert warns and "matches no agenda item" in warns[0]
    s, fb = score(md(tree=tree), tree, gold, [], [])  # unresolved heading skipped, not scored as a miss
    assert s == 1.0, fb


def test_template_round_trip() -> None:
    assert template(SEED) == BASELINE and BASELINE.startswith(PREFIX)
    check_template(template("Use {braces} freely."))


def test_negated_note_does_not_trip_forbidden() -> None:
    gold = parse_gold(
        {"workspace": "bishopric", "forbidden": [{"keywords": [["varga"], ["accepted"]], "why": "only extended"}]}, "t"
    )
    tree = parse_text("Callings\n")
    ok = note_on(tree, "Calling extended to Sister Varga; she has not yet accepted.")
    bad = note_on(tree, "Sister Varga accepted the calling.")
    assert score(md(ok, tree=tree), tree, gold, [], [])[0] == 1.0
    assert score(md(bad, tree=tree), tree, gold, [], [])[0] == pytest.approx(1 / 1.2)


def note_on(tree: list[AgendaNode], text: str) -> dict[str, Any]:
    return {**note("Ward history", text), "agenda_node_id": tree[0].id}


def test_null_item_counts_anywhere() -> None:
    gold = parse_gold(
        {"workspace": "ward_council", "expected": [{"item": None, "keywords": [["nursery"]], "follow_up": True}]}, "t"
    )
    s, fb = score(md(GOOD[1]), TREE, gold, [], [])
    assert s == 1.0, fb
    s, fb = score(md(GOOD[0]), TREE, gold, [], [])
    assert s == 0.0 and "Missing: no note about 'nursery' anywhere in the notes." in "\n".join(fb)


def test_also_headings_count_without_lifting_false_absence() -> None:
    gold = parse_gold(
        {"workspace": "ward_council", "expected": [
            {"item": "Ward history", "also": ["Activity coordination"], "keywords": [["album"]], "follow_up": None}]},
        "t",
    )  # fmt: skip
    s, fb = score(md(note("Activity coordination", "Album due Friday.")), TREE, gold, [], [])
    assert "but it appeared under" not in "\n".join(fb)
    assert "False 'Not discussed.' for 'Ward history'" in "\n".join(fb) and s == pytest.approx(1 / 1.15)
    s, fb = score(md(note("Reimbursement guidelines", "Album due Friday.")), TREE, gold, [], [])
    assert "Expected 'album' under 'Ward history' but it appeared under 'Reimbursement guidelines'" in "\n".join(fb)
    with pytest.raises(ValueError):
        parse_gold(
            {"workspace": "bishopric", "expected": [{"item": None, "also": ["Ward history"], "keywords": [["x"]]}]}, "t"
        )


def test_topics_max_headings_and_detail_ratio() -> None:
    tree = parse_text("Food bank project\nMeals\n")  # stands in for headings the app proposed
    n = {**note_on(tree, "Volunteers sort cans on the 14th."), "agenda_node_id": tree[1].id}
    gold = parse_gold(
        {"workspace": "ward_council", "topics": [["food bank"], [["volunteer"], ["cans"]], ["missionar"]],
         "max_headings": 1, "reference_notes": "ref.txt"},
        "t",
    )  # fmt: skip
    gold.reference_words = 100  # the note has 6 words: ratio < 0.5
    s, fb = score(md(n, tree=tree), tree, gold, [], [])
    text = "\n".join(fb)
    assert "Proposed headings missed topic 'missionar'" in text
    assert "Too many proposed headings (2, at most 1)" in text
    assert "Notes are much shorter than the reference (6 vs 100 words)" in text
    assert s == pytest.approx((2 / 3) / (1 + 0.02 + 0.05))
    gold.reference_words, gold.max_headings = 2, None  # ratio 3 > 2
    assert "much longer" in "\n".join(score(md(n, tree=tree), tree, gold, [], [])[1])
    gold.reference_words = 8
    assert score(md(n, tree=tree), tree, gold, [], [])[0] == pytest.approx(2 / 3)


def test_reference_words_skip_headings_placeholders_and_follow_up_list() -> None:
    ref = "SYNTHETIC x\n# Notes\n- Budget\n  - Two new tables approved.\n  - Not discussed.\nAgenda:\nNext Meeting – Follow-Up Items\n- Buy tables\n"
    assert reference_words(ref) == 4


def test_gold_shapes_rejected() -> None:
    bad: dict[str, Any]
    for bad in (
        {"max_headings": 0},
        {"reference_notes": 3},
        {"topics": [[]]},
        {"expected": [{"item": 1, "keywords": [["x"]]}]},
    ):
        with pytest.raises(ValueError):
            parse_gold({"workspace": "bishopric", **bad}, "t")
    assert parse_gold({"workspace": "bishopric", "gpt_errors": ["ignored"]}, "t").topics == []


def test_keywords_match_word_starts_only() -> None:
    from prompt_tuning.metric import matches

    assert matches("Reimbursement by Dec 31.", [["reimburs"], ["dec"]])
    assert not matches("Duties were assigned.", [["uti"]])  # inside a word: coincidence, not the fact
    assert not matches("Due on the 23rd.", [["3rd"]])


def test_measures_separate_capture_filing_absence_and_wrong_content() -> None:
    from prompt_tuning.metric import measures

    misfiled = note("Activity coordination", "Activity receipts reimbursed before Dec 31.")
    c = measures(md(misfiled, note("Ward history", "Binder done.")), TREE, GOLD)
    assert (c["facts"], c["captured"], c["on_item"]) == (2, 1, 0)
    assert (c["items"], c["false_absent"]) == (2, 2)
    assert c["wrong"] == 2  # forbidden activity+reimburs, and a note under the undiscussed "Ward history"
    good = measures(md(*GOOD), TREE, GOLD)
    assert (good["captured"], good["on_item"], good["false_absent"], good["wrong"]) == (2, 2, 0, 0)
