"""Ollama adapter. Local loopback hosts only; no fallback to anything else."""

from __future__ import annotations

import json
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
        # timeout=1800: a dense 27-31B model at ~20 tok/s needs ~10 min for a full 12k-token reply.
        # trust_env=False: ignore HTTP(S)_PROXY / OS proxy settings, which httpx would otherwise apply even to
        # loopback URLs; follow_redirects=False: never let a 3xx send the transcript somewhere else.
        self._client = ollama.Client(host=self.host, timeout=1800, trust_env=False, follow_redirects=False)

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
        prompt, fmt = req.prompt, req.json_schema
        if req.think and fmt is not None:
            # Ollama 0.33 + qwen3.6: a format grammar together with thinking garbles the JSON ('{"answer": "}"').
            # Ask for the schema in words instead; the caller validates the reply strictly either way.
            prompt += "\n\nReply with only a JSON object matching this JSON Schema:\n" + json.dumps(fmt)
            fmt = None
        msg: dict[str, Any] = {"role": "user", "content": prompt}
        if req.images:
            msg["images"] = req.images
        kwargs: dict[str, Any] = {
            "model": req.model,
            "messages": [{"role": "system", "content": req.system}, msg],
            "format": fmt,
            "options": {"temperature": req.temperature, "num_ctx": req.num_ctx, "num_predict": req.max_tokens},
            "stream": False,
        }
        try:
            # Thinking is off unless the request asks for it: reasoning models otherwise spend the whole output
            # budget on hidden thinking and return "". Some models reject the flag, so fall back to the plain call.
            try:
                resp = self._client.chat(**kwargs, think=req.think)
            except ollama.ResponseError as e:
                if "think" not in str(e).lower():
                    raise
                resp = self._client.chat(**kwargs)
            content = resp.message.content or ""
            if not content.strip():
                resp = self._client.chat(**kwargs)
                content = resp.message.content or ""
        except Exception as e:  # noqa: BLE001
            raise ProviderError(f"Ollama request failed for model {req.model!r}: {e}") from e
        if not content.strip():
            raise ProviderError(
                f"Model {req.model!r} returned no content (output budget of {req.max_tokens} tokens exhausted, "
                "or the model emitted only reasoning). Increase max output tokens or choose another model."
            )
        return content
