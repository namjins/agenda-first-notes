"""The think setting reaches Ollama; off by default; with it on, the schema goes into the prompt. No network: the client is a stub."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agenda_first_notes.llm.base import GenerateRequest
from agenda_first_notes.llm.ollama_provider import OllamaProvider


def test_think_flag_passed_through() -> None:
    p = OllamaProvider("http://127.0.0.1:11434")
    seen: list[Any] = []

    def chat(**kw: Any) -> Any:
        seen.append((kw.get("think"), kw["format"], "JSON Schema" in kw["messages"][1]["content"]))
        return SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))

    p._client = SimpleNamespace(chat=chat)  # type: ignore[assignment]
    schema = {"type": "object"}
    p.generate(GenerateRequest(model="m", system="s", prompt="p", json_schema=schema))
    p.generate(GenerateRequest(model="m", system="s", prompt="p", json_schema=schema, think=True))
    # thinking garbles format-constrained JSON in Ollama 0.33, so the schema moves into the prompt
    assert seen == [(False, schema, False), (True, None, True)]
