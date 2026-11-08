"""Shared fixtures: isolated data dir per test, fake local model provider, authenticated client."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "afn-data"
    monkeypatch.setenv("AFN_DATA_DIR", str(d))
    monkeypatch.setenv("AFN_PROVIDER", "fake")
    from agenda_first_notes import workspaces

    workspaces.reset_engines()
    yield d
    workspaces.reset_engines()


def node_ids(prompt: str) -> dict[str, str]:
    """Map agenda text -> node id from the outline embedded in a prompt."""
    return {m.group(2).strip(): m.group(1) for m in re.finditer(r"- \[(n[0-9a-f]+)\] (.+)", prompt)}


def finding(node: str | None, text: str, quotes: list[str], **kw: object) -> dict[str, object]:
    return {
        "agenda_node_id": node,
        "text": text,
        "category": "discussion",
        "uncertainty": "low",
        "follow_up": False,
        "owner": None,
        "calling_event": None,
        "evidence": quotes,
        **kw,
    }


def ward_council_handler(req) -> str:  # type: ignore[no-untyped-def]
    """Deterministic 'model' for the synthetic ward council transcript."""
    if req.purpose == "review":
        return json.dumps({"findings": []})
    if req.purpose == "agenda":
        return json.dumps(
            {
                "topics": [
                    {"text": "Meal rotation", "children": []},
                    {"text": "Youth conference", "children": [{"text": "Bus quote"}]},
                ]
            }
        )
    ids = node_ids(req.prompt)
    out = [
        finding(
            ids.get("Ministering"),
            "Ministering interviews reported about eighty percent complete.",
            ["Interviews are mostly done, we're at about eighty percent."],
            category="status_change",
        ),
        finding(
            ids.get("Compassionate service plan"),
            "Decided to adopt the meal rotation starting next month.",
            ["we decided to adopt the meal rotation starting next month"],
            category="decision",
        ),
        finding(
            ids.get("Compassionate service plan"),
            "Rowan Castellano will draft the rotation list by the fifteenth.",
            [
                "Rowan Castellano, can you draft the rotation list?",
                "Yes, I'll draft the rotation list by the fifteenth.",
            ],
            category="assignment",
            owner="Rowan Castellano",
            follow_up=True,
        ),
        finding(
            ids.get("Temple trip"),
            "May or June proposed for the temple trip; no decision reached.",
            ["maybe we could do it in May? Or June?", "No decision today."],
            category="proposed_decision",
            uncertainty="medium",
            follow_up=True,
        ),
        # Overconfident claim: evidence is hedged; deterministic check must flag it.
        finding(
            ids.get("Activity days schedule"),
            "Activity days will be moved.",
            ["we may need to move it, we'll see"],
            category="decision",
        ),
        # Inferred owner not in evidence: must be stripped.
        finding(
            ids.get("Youth Conference planning"),
            "Theme suggested; nothing settled.",
            ["suggested a theme but we haven't settled on anything"],
            category="proposed_decision",
            uncertainty="medium",
            owner="Avery Lindqvist",
        ),
        finding(
            ids.get("Youth Conference planning"),
            "Pat will look into the bus quote.",
            ["Pat, could you look into the bus quote? Pat: I'll look into the bus quote."],
            category="assignment",
            owner="Pat",
            follow_up=True,
        ),
        # Invented quote: must be rejected as unsupported.
        finding(
            ids.get("Closing"),
            "Closing prayer by the bishop.",
            ["The bishop offered the closing prayer."],
            category="discussion",
        ),
    ]
    return json.dumps({"findings": out})


@pytest.fixture
def fake_provider(request: pytest.FixtureRequest):  # type: ignore[no-untyped-def]
    from agenda_first_notes.llm import set_provider
    from agenda_first_notes.llm.fake import FakeProvider

    handler: Callable = getattr(request, "param", ward_council_handler)
    p = FakeProvider(handler)
    set_provider(p)
    yield p
    set_provider(None)


@pytest.fixture
def client(data_dir: Path, fake_provider) -> TestClient:  # type: ignore[no-untyped-def]
    from agenda_first_notes import config
    from agenda_first_notes.main import create_app

    s = config.load_settings()
    s.models.primary_model = "fake-model"
    s.models.max_review_rounds = 2
    config.save_settings(s)
    app = create_app(frontend_dist=None)
    c = TestClient(app, base_url="http://127.0.0.1:8765", headers={"X-AFN-Token": config.api_token()})
    c.__enter__()
    yield c
    c.__exit__(None, None, None)


def run_meeting(client: TestClient, ws: str, agenda: str | None, transcript: str, date: str = "2026-03-01") -> str:
    """Drive the workflow synchronously through the API; returns meeting id."""
    m = client.post(f"/api/w/{ws}/meetings", json={"title": "Test", "meeting_date": date}).json()
    mid = m["id"]
    if agenda is not None:
        assert (
            client.put(f"/api/w/{ws}/meetings/{mid}/agenda", json={"text": agenda, "source": "text"}).status_code == 200
        )
        assert client.post(f"/api/w/{ws}/meetings/{mid}/agenda/approve").status_code == 200
    assert client.put(f"/api/w/{ws}/meetings/{mid}/transcript", json={"text": transcript}).status_code == 200
    return mid


def process_sync(ws_id: str, mid: str) -> dict:  # type: ignore[type-arg]
    from agenda_first_notes import services
    from agenda_first_notes.workspaces import get_workspace

    return services.process_meeting(get_workspace(ws_id), mid)


def read(name: str) -> str:
    return (FIXTURES / name).read_text("utf-8")


@pytest.fixture(autouse=True)
def _no_real_ollama(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any accidental attempt to construct the real provider fails loudly in tests.
    import agenda_first_notes.llm as llm

    original = llm.get_provider

    def guarded():  # type: ignore[no-untyped-def]
        if llm._provider is None:
            raise AssertionError("Test tried to use the real Ollama provider")
        return original()

    monkeypatch.setattr(llm, "get_provider", guarded)
    for mod in (
        "agenda_first_notes.services",
        "agenda_first_notes.api",
        "agenda_first_notes.ingest",
    ):
        import importlib

        m = importlib.import_module(mod)
        if hasattr(m, "get_provider"):
            monkeypatch.setattr(m, "get_provider", guarded)
    _ = os
