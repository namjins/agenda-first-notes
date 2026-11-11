"""Discovery of nested meeting trees and the no-agenda path through the app, with the app's FakeProvider (no Ollama).
SYNTHETIC content only."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from agenda_first_notes.llm.fake import FakeProvider, demo_handler

from prompt_tuning.app import BASELINE, App
from prompt_tuning.metric import load_meetings, score

TRANSCRIPT = (
    "SYNTHETIC – invented\n"
    "Garden plots: the garden plots open on April third, Sister Brannigan-Oduya will hand out keys.\n"
    "Choir practice: choir practice moves to Thursday evenings.\n"
    "Bake sale: the bake sale is cancelled this year.\n"
)


def write(d: Path, gold: dict[str, object], agenda: str | None = None) -> None:
    d.mkdir(parents=True)
    (d / "transcript.txt").write_text(TRANSCRIPT, "utf-8")
    (d / "gold.json").write_text(json.dumps(gold), "utf-8")
    if agenda is not None:
        (d / "agenda.txt").write_text(agenda, "utf-8")


GOLD = {
    "workspace": "bishopric",
    "expected": [{"item": None, "keywords": [["garden"], ["april"]], "follow_up": None}],
    "topics": [["garden"], ["choir"], ["missionar"]],
    "max_headings": 2,
}


def test_discovery_nested_dirs_with_spaces(tmp_path: Path) -> None:
    write(tmp_path / "Ward Council" / "2026 01 05", {**GOLD, "workspace": "ward_council"}, "Garden plots\n")
    write(tmp_path / "Bishopric" / "2026 01 04", GOLD)
    (tmp_path / "Bishopric" / "no gold").mkdir()
    (tmp_path / "Bishopric" / "no gold" / "transcript.txt").write_text("SYNTHETIC", "utf-8")
    ms = load_meetings(tmp_path)
    assert [m.name for m in ms] == ["Bishopric/2026 01 04", "Ward Council/2026 01 05"]
    assert ms[0].agenda is None and ms[1].agenda == "Garden plots\n"
    assert ms[0].transcript.startswith("Garden plots:")  # SYNTHETIC marker line dropped


def test_no_agenda_meeting_runs_proposal_then_extraction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFN_DATA_DIR", str(tmp_path / "unused"))  # App overrides it; restored after the test
    write(tmp_path / "data" / "m", GOLD)
    (m,) = load_meetings(tmp_path / "data")
    fake = FakeProvider(demo_handler)
    app = App("fake-model", "http://127.0.0.1:11434", 0, provider=fake)
    try:
        r = app.run(m, BASELINE)
    finally:
        app.close()
    assert [q.purpose for q in fake.requests][0] == "agenda"  # proposal first, then extraction
    assert len(r.tree) == 3 and r.tree[1].text == "choir practice moves to Thursday evenings."
    s, fb = score(r.markdown, r.tree, m.gold, r.high, r.errors)
    text = "\n".join(fb)
    assert "Proposed headings missed topic 'missionar'" in text and "Too many proposed headings (3, at most 2)" in text
    assert "1/1 expected facts found, 2/3 topics covered" in text
    assert 0 < s < 1
