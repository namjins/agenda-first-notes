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
