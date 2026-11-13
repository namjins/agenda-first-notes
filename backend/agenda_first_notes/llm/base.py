from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class ProviderError(RuntimeError):
    pass


@dataclass
class ModelInfo:
    name: str
    size_bytes: int | None = None
    parameter_size: str | None = None
    family: str | None = None
    capabilities: list[str] = field(default_factory=list)  # e.g. ["completion", "vision"]


@dataclass
class GenerateRequest:
    model: str
    system: str
    prompt: str
    json_schema: dict[str, Any] | None = None
    images: list[bytes] = field(default_factory=list)
    temperature: float = 0.1
    num_ctx: int = 32768
    max_tokens: int = 4096
    purpose: str = "extract"  # extract | file | review | agenda | merge | ocr | test
    think: bool = False  # let reasoning models think first (slower; uses part of max_tokens)


class ModelProvider(Protocol):
    def health(self) -> dict[str, Any]: ...
    def list_models(self) -> list[ModelInfo]: ...
    def generate(self, req: GenerateRequest) -> str: ...
