"""Model provider port. Only local providers exist; the registry is swapped in tests."""

from __future__ import annotations

from .base import GenerateRequest, ModelInfo, ModelProvider, ProviderError

_provider: ModelProvider | None = None


def get_provider() -> ModelProvider:
    global _provider
    if _provider is None:
        import os

        if os.environ.get("AFN_PROVIDER") == "fake":  # tests / demos only; never the default
            from .fake import FakeProvider, demo_handler

            _provider = FakeProvider(demo_handler)
        else:
            from .. import config
            from .ollama_provider import OllamaProvider

            _provider = OllamaProvider(config.load_settings().models.ollama_host)
    return _provider


def set_provider(p: ModelProvider | None) -> None:
    global _provider
    _provider = p


__all__ = [
    "GenerateRequest",
    "ModelInfo",
    "ModelProvider",
    "ProviderError",
    "get_provider",
    "set_provider",
]
