"""Optional integration test against a real local Ollama model. Skipped unless AFN_REAL_MODEL is set.

Run:  AFN_REAL_MODEL=qwen3.6:35b-a3b uv run pytest tests/test_real_model.py -s
Uses only the synthetic fixtures. Asserts the invariants that must hold regardless of model quality.
"""

from __future__ import annotations

import os

import pytest

from agenda_first_notes import config, services
from agenda_first_notes.llm import set_provider
from agenda_first_notes.llm.ollama_provider import OllamaProvider
from agenda_first_notes.workspaces import get_workspace
from tests.conftest import read

MODEL = os.environ.get("AFN_REAL_MODEL")
pytestmark = pytest.mark.skipif(not MODEL, reason="set AFN_REAL_MODEL=<ollama model> to run")


def test_real_model_ward_council(data_dir) -> None:  # type: ignore[no-untyped-def]
    assert MODEL
    provider = OllamaProvider("http://127.0.0.1:11434")
    set_provider(provider)
    s = config.load_settings()
    s.models.primary_model = MODEL
    s.models.max_review_rounds = 1
    s.models.max_output_tokens = int(os.environ.get("AFN_REAL_MODEL_TOKENS", "12288"))
    config.save_settings(s)
    from agenda_first_notes import db

    ws = get_workspace("ward_council")
    db.migrate(ws)
    m = services.create_meeting(ws, "Real model run", "2026-03-01")
    services.set_agenda(ws, m.id, text=read("ward_council_agenda.txt"), source="text")
    services.approve_agenda(ws, m.id)
    transcript = read("ward_council_transcript.txt")
    services.set_transcript(ws, m.id, transcript, source="paste")
    result = services.process_meeting(ws, m.id, progress=lambda p, msg: print(f"{p:.0%} {msg}"), provider=provider)
    md = services.render_meeting(ws, m.id)
    print("\n" + md)
    from sqlalchemy import select

    from agenda_first_notes.models import Note, ReviewFinding
    from agenda_first_notes.workspaces import session_for

    with session_for(ws) as sess:
        notes = list(sess.scalars(select(Note).where(Note.meeting_id == m.id)))
        findings = list(sess.scalars(select(ReviewFinding).where(ReviewFinding.meeting_id == m.id)))
    print(f"state={result['state']} notes={len(notes)} findings={len(findings)}")
    for f in findings:
        print(f"  [{f.severity}] {f.category}: {f.message} ({f.action})")
    # Invariants independent of model quality:
    for n in notes:
        if n.status not in ("unsupported", "manual_unverified"):
            for ev in n.evidence:
                assert transcript[ev["start"] : ev["end"]] == ev["quote"]  # evidence offsets are real
        if n.owner:
            assert n.owner.lower() in " ".join(e["quote"] for e in n.evidence).lower()
    for heading in ["- Organizations", "  - Relief Society", "    - Compassionate service plan", "- Closing"]:
        assert heading in md
    assert md.count("Next Meeting – Follow-Up Items") == 1
    assert result["coverage_complete"] is True
