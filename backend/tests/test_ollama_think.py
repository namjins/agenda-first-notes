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
        return iter([SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    p._client = SimpleNamespace(chat=chat)  # type: ignore[assignment]
    schema = {"type": "object"}
    p.generate(GenerateRequest(model="m", system="s", prompt="p", json_schema=schema))
    p.generate(GenerateRequest(model="m", system="s", prompt="p", json_schema=schema, think=True))
    # thinking garbles format-constrained JSON in Ollama 0.33, so the schema moves into the prompt
    assert seen == [(False, schema, False), (True, None, True)]


def test_a_looping_reply_is_cut_off_early() -> None:
    p = OllamaProvider("http://127.0.0.1:11434")
    finding = '{"text": "The same finding again", "evidence": ["' + "a quote " * 40 + '"]}, '
    sent: list[int] = []

    def chat(**kw: Any) -> Any:
        def gen() -> Any:
            yield SimpleNamespace(message=SimpleNamespace(content='{"findings": ['))
            for _ in range(500):
                sent.append(1)
                yield SimpleNamespace(message=SimpleNamespace(content=finding))

        return gen()

    p._client = SimpleNamespace(chat=chat)  # type: ignore[assignment]
    out = p.generate(GenerateRequest(model="m", system="s", prompt="p"))
    assert len(sent) < 20 and out.count(finding) >= 4  # stopped after a few repeats, not at the token cap
    # an ordinary reply with different findings is returned whole
    parts = ['{"findings": ['] + [f'{{"text": "finding {i}", "evidence": ["{"q" * 400}{i}"]}}, ' for i in range(30)]
    p._client = SimpleNamespace(
        chat=lambda **kw: iter(SimpleNamespace(message=SimpleNamespace(content=c)) for c in parts)
    )  # type: ignore[assignment]
    assert p.generate(GenerateRequest(model="m", system="s", prompt="p")) == "".join(parts)
