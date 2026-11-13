"""Scriptable provider for tests. Never used in production."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import GenerateRequest, ModelInfo


class FakeProvider:
    def __init__(self, handler: Callable[[GenerateRequest], str], models: list[str] | None = None) -> None:
        self.handler = handler
        self.models = models or ["fake-model"]
        self.requests: list[GenerateRequest] = []

    def health(self) -> dict[str, Any]:
        return {"connected": True, "host": "fake", "models": self.models}

    def list_models(self) -> list[ModelInfo]:
        return [ModelInfo(name=m, capabilities=["completion", "vision"]) for m in self.models]

    def generate(self, req: GenerateRequest) -> str:
        self.requests.append(req)
        return self.handler(req)


def demo_handler(req: GenerateRequest) -> str:
    """Rule-based stand-in used when AFN_PROVIDER=fake (end-to-end tests, CI, UI demos).

    Extraction: for every topic-level agenda item, cite transcript lines that mention a word from it.
    Review/agenda: trivial. Deliberately dumb; it exists so the full workflow runs without a model.
    """
    import json
    import re

    if req.purpose == "review":
        return json.dumps({"findings": []})
    if req.purpose == "file":
        return json.dumps({"placements": []})  # keep the extraction's placement
    if req.purpose == "merge":
        return json.dumps({"topics": []})  # keep the proposed headings
    if req.purpose == "agenda":
        lines = [
            ln.strip()
            for ln in req.prompt.split("<<<TRANSCRIPT", 1)[-1].split("TRANSCRIPT>>>")[0].splitlines()
            if ln.strip()
        ]
        topics = [{"text": ln.split(":", 1)[-1].strip()[:60], "children": []} for ln in lines[:3]]
        return json.dumps({"topics": topics})
    if req.purpose in ("ocr", "test"):
        return (
            '{"ok": true, "echo": "synthetic test"}'
            if req.purpose == "test"
            else "Opening\nBusiness\n  Item one\nClosing"
        )
    excerpt = req.prompt.split("<<<TRANSCRIPT", 1)[-1].split("TRANSCRIPT>>>")[0]
    nodes = re.findall(r"- \[(n[0-9a-f]+)\] (.+)", req.prompt)
    findings = []
    for nid, text in nodes:
        words = [w.lower() for w in re.findall(r"[A-Za-z]{4,}", text)]
        for line in excerpt.splitlines():
            if words and any(w in line.lower() for w in words) and len(line.strip()) > 10:
                # cut at a word boundary: a clipped name ("Sam Alp") is not in the evidence
                shown = line.strip() if len(line.strip()) <= 140 else line.strip()[:140].rsplit(" ", 1)[0]
                findings.append(
                    {
                        "agenda_node_id": nid,
                        "text": f"Discussion indicated: {shown}",
                        "category": "discussion",
                        "uncertainty": "medium",
                        "follow_up": "follow" in line.lower(),
                        "owner": None,
                        "calling_event": None,
                        "evidence": [line.strip()],
                    }
                )
                break
    return json.dumps({"findings": findings})
