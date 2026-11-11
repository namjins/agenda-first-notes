"""Member-name flow: list discovery, the simulated user's confirmations, name recall, placeholders for real data, the
member-name leak guard and several data roots. SYNTHETIC invented names only; the app's FakeProvider (no Ollama)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agenda_first_notes import render, services
from agenda_first_notes.agenda import parse_text
from agenda_first_notes.config import REPO_ROOT
from agenda_first_notes.llm.fake import FakeProvider, demo_handler

from prompt_tuning.app import BASELINE, SEED, App
from prompt_tuning.cli import COMPONENT, ExtractAdapter, _guard
from prompt_tuning.metric import SYNTHETIC, load_meetings, score

CSV = (
    "Preferred Name,Full Name,Spouse Name,Callings with Date Sustained,SYNTHETIC\n"
    '"Ross, Robert","Ross, Robert James","Ross, Mary",Primary Teacher (5 Jan 2025)\n'
    '"Moss, Robert","Moss, Robert Lee",,Elders Quorum Instructor (2 Feb 2024)\n'
    '"Ross, Mary","Ross, Mary Ann","Ross, Robert",Relief Society Teacher (6 Jun 2024)\n'
)
AGENDA = "Primary\nElders quorum\n"
TRANSCRIPT = (
    "SYNTHETIC – invented\n"
    "Primary update: bob rosse said he can teach the sunbeams next week.\n"
    "Elders quorum: Sister Rass will bring the treats, and the service project is on track.\n"
)
GOLD: dict[str, Any] = {
    "workspace": "ward_council",
    "expected": [{"item": "Primary", "keywords": [["sunbeam"]], "follow_up": None}],
    "names": [{"heard": "bob rosse", "member": "Ross, Robert James"}],
}


def meeting(d: Path, gold: dict[str, Any] = GOLD, agenda: str | None = AGENDA) -> None:
    d.mkdir(parents=True)
    (d / "transcript.txt").write_text(TRANSCRIPT, "utf-8")
    (d / "gold.json").write_text(json.dumps(gold), "utf-8")
    if agenda is not None:
        (d / "agenda.txt").write_text(agenda, "utf-8")


def test_nearest_list_up_to_the_root(tmp_path: Path) -> None:
    root = tmp_path / "data"
    (tmp_path / "members.csv").write_text(CSV, "utf-8")  # above the root: never used
    meeting(root / "WC" / "a")
    meeting(root / "WC" / "b", {**GOLD, "names": []})
    (root / "WC" / "Members.csv").write_text(CSV, "utf-8")
    (root / "WC" / "b" / "members.csv").write_text(CSV.replace("Ross, Robert James", "Ross, Robert Jay"), "utf-8")
    meeting(root / "BP" / "c", {**GOLD, "names": []})
    a, b, c = sorted(load_meetings(root), key=lambda m: m.name[-1])
    assert a.members_path is not None and a.members_path.parent == root / "WC" and len(a.members) == 3
    assert b.members_path == root / "WC" / "b" / "members.csv" and b.members[0]["full_name"] == "Ross, Robert Jay"
    assert c.members_path is None and c.members == []
    (g,) = a.gold.names
    assert g.forms == ["Robert James Ross", "Robert Ross"] and not g.exact
    assert g.label == "gold name #1"  # tmp_path is not data/synthetic: no names in feedback


def test_gold_member_must_be_in_the_list(tmp_path: Path) -> None:
    meeting(tmp_path / "m", {**GOLD, "names": [{"heard": "bob rosse", "member": "Nobody, Invented"}]})
    (tmp_path / "members.csv").write_text(CSV, "utf-8")
    with pytest.raises(ValueError, match=r"names\[0\] member is not a Full Name") as e:
        load_meetings(tmp_path)
    assert "Nobody" not in str(e.value)


def test_several_roots_prefixed_and_sorted(tmp_path: Path) -> None:
    meeting(tmp_path / "zeta" / "m1")
    meeting(tmp_path / "alpha" / "m2")
    meeting(tmp_path / "alpha" / "m1")
    ms = load_meetings(tmp_path / "zeta", tmp_path / "alpha")
    assert [m.name for m in ms] == ["alpha/m1", "alpha/m2", "zeta/m1"]
    meeting(tmp_path / "other" / "zeta" / "m1")
    with pytest.raises(SystemExit, match="repeat"):
        load_meetings(tmp_path / "zeta", tmp_path / "other" / "zeta")


def test_guard_applies_when_any_root_is_real(tmp_path: Path) -> None:
    _guard([SYNTHETIC], REPO_ROOT / "tools" / "prompt_tuning" / "out" / "x.txt")  # synthetic only: allowed
    with pytest.raises(SystemExit, match="Refusing"):
        _guard([SYNTHETIC, tmp_path], REPO_ROOT / "tools" / "prompt_tuning" / "out" / "x.txt")


def test_simulated_user_confirms_only_gold_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AFN_DATA_DIR", str(tmp_path / "unused"))  # App overrides it; restored after the test
    meeting(tmp_path / "data" / "m")
    (tmp_path / "data" / "members.csv").write_text(CSV, "utf-8")
    (m,) = load_meetings(tmp_path / "data")
    calls: list[tuple[str, str]] = []
    real = services.confirm_name

    def spy(ws: Any, mid: str, heard: str, member_id: str, status: str = "confirmed") -> Any:
        calls.append((heard, member_id))
        return real(ws, mid, heard, member_id, status)

    monkeypatch.setattr(services, "confirm_name", spy)
    app = App("fake-model", "http://127.0.0.1:11434", 0, provider=FakeProvider(demo_handler))
    try:
        heard = {x["heard"] for x in _suggestions(app, m)}
        assert {"bob rosse", "Sister Rass"} <= heard  # two suggestions; only one is gold
        r = app.run(m, BASELINE)
        ross = next(x["id"] for x in services.list_members() if x["full_name"] == "Ross, Robert James")
    finally:
        app.close()
    assert calls == [("bob rosse", ross)]
    assert r.usable == {0}
    st = app.name_stats[m.name]
    assert (st.total, st.exact, st.suggested) == (1, 0, 1)
    _, fb = score(r.markdown, r.tree, m.gold, r.high, r.errors, r.usable)
    assert "/1 member names used" in fb[0]  # the confirmed name is scored


def _suggestions(app: App, m: Any) -> list[dict[str, Any]]:
    """Suggestions for the meeting as the app shows them, from a scratch meeting (deleted again)."""
    from agenda_first_notes.workspaces import get_workspace

    ws = get_workspace(m.gold.workspace)
    app._use_list(m)
    x = services.create_meeting(ws, "probe", None)
    services.set_transcript(ws, x.id, m.transcript, source="paste")
    out: list[dict[str, Any]] = services.name_suggestions(ws, x.id)["suggestions"]
    services.delete_meeting(ws, x.id)
    return out


def _md(*texts: str) -> tuple[str, list[Any]]:
    tree = parse_text(AGENDA)
    ev = [{"start": 0, "end": 1, "quote": "x", "transcript_version_id": None}]
    notes = [{"agenda_node_id": tree[0].id, "text": t, "follow_up": False, "owner": None, "category": "decision",
              "evidence": ev, "status": "verified", "origin": "model", "position": i}
             for i, t in enumerate(texts)]  # fmt: skip
    return render.render(title="T", meeting_date=None, tree=tree, notes=notes, coverage_complete=True), tree


def test_names_recall_and_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prompt_tuning.metric.SYNTHETIC", tmp_path.resolve())  # treat this tree as synthetic
    meeting(tmp_path / "m")
    (tmp_path / "members.csv").write_text(CSV, "utf-8")
    (m,) = load_meetings(tmp_path)
    md, tree = _md("Sunbeams class taught next week.")
    s, fb = score(md, tree, m.gold, [], [], {0})
    assert "1/1 expected facts found, 0/1 member names used" in fb[0] and s == pytest.approx(0.5)
    assert "Confirmed name 'Robert Ross' for 'bob rosse' never used in the notes." in fb
    md, tree = _md("Robert Ross will teach the sunbeams next week.")
    s, fb = score(md, tree, m.gold, [], [], {0})
    assert "1/1 member names used" in fb[0] and s == 1.0
    s, fb = score(*_md("Sunbeams class taught next week."), m.gold, [], [], set())  # not confirmed: not scored
    assert s == 1.0 and "member names" not in fb[0]


def test_real_data_feedback_uses_placeholders(tmp_path: Path) -> None:
    meeting(tmp_path / "m")
    (tmp_path / "members.csv").write_text(CSV, "utf-8")
    (m,) = load_meetings(tmp_path)
    s, fb = score(*_md("Sunbeams class taught next week."), m.gold, [], [], {0})
    assert "Confirmed name gold name #1 never used in the notes." in fb
    assert not any(w in line.lower() for line in fb for w in ("ross", "robert", "bob rosse"))


def test_leak_guard_on_member_names(tmp_path: Path) -> None:
    meeting(tmp_path / "m")
    (tmp_path / "members.csv").write_text(CSV, "utf-8")
    ms = load_meetings(tmp_path)
    adapter = ExtractAdapter(None, ms)  # type: ignore[arg-type]  # the guard scores before the app is used
    for leak in ("Robert Ross", "mary ross", "any note about Moss"):  # first+last; surname of 4+ letters
        out = adapter.evaluate(ms, {COMPONENT: f"{SEED}\n- Spell {leak} carefully."}, capture_traces=True)
        assert out.scores == [0.0]
        assert out.trajectories and out.trajectories[0]["Problems"] == (
            "Instructions contain a member's name; write general rules only."
        )
    assert adapter.names is not None and not adapter.names.search("robert mary")  # first names alone are allowed
