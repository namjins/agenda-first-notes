"""Ollama adapter. Local loopback hosts only; no fallback to anything else."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import ollama

from .base import GenerateRequest, ModelInfo, ProviderError

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]", "0.0.0.0"}  # noqa: S104 - allowlist, not a bind


def assert_local_host(host: str) -> str:
    u = urlparse(host if "//" in host else f"http://{host}")
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK_HOSTS:
        raise ProviderError(f"Only a local Ollama endpoint is allowed; refused {host!r}")
    return host


class OllamaProvider:
    def __init__(self, host: str) -> None:
        self.host = assert_local_host(host)
        self._client = ollama.Client(host=self.host, timeout=600)

    def health(self) -> dict[str, Any]:
        try:
            models = self.list_models()
        except ProviderError as e:
            return {"connected": False, "host": self.host, "error": str(e), "models": []}
        return {"connected": True, "host": self.host, "models": [m.name for m in models]}

    def list_models(self) -> list[ModelInfo]:
        try:
            resp = self._client.list()
        except Exception as e:  # noqa: BLE001 - network/library errors surface as one type
            raise ProviderError(f"Cannot reach Ollama at {self.host}: {e}") from e
        out: list[ModelInfo] = []
        for m in resp.models or []:
            details = m.details
            info = ModelInfo(
                name=m.model or "",
                size_bytes=m.size,
                parameter_size=details.parameter_size if details else None,
                family=details.family if details else None,
            )
            try:
                show = self._client.show(info.name)
                info.capabilities = list(show.capabilities or [])
            except Exception:  # noqa: BLE001 - capabilities are advisory
                info.capabilities = []
            out.append(info)
        return out

    def generate(self, req: GenerateRequest) -> str:
        msg: dict[str, Any] = {"role": "user", "content": req.prompt}
        if req.images:
            msg["images"] = req.images
        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": "system", "content": req.system}, msg],
            "format": req.json_schema,
            "options": {"temperature": req.temperature, "num_ctx": req.num_ctx, "num_predict": req.max_tokens},
            "stream": False,
        }
        try:
            try:
                # Reasoning models otherwise spend the whole output budget on hidden thinking and return "".
                resp = self._client.chat(**kwargs, think=False)
            except ollama.ResponseError as e:
                if "think" not in str(e).lower():
                    raise
                resp = self._client.chat(**kwargs)
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Ollama request failed for model {req.model!r}: {e}") from e
        content = resp.message.content or ""
        if not content.strip():
            raise ProviderError(
                f"Model {req.model!r} returned no content (output budget of {req.max_tokens} tokens exhausted, "
                "or the model emitted only reasoning). Increase max output tokens or choose another model."
            )
        return content
