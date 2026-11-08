"""Local-only file ingestion: agenda images (OCR via local vision model or Tesseract) and reference docs."""

from __future__ import annotations

import io
import shutil
from typing import Any

from PIL import Image

from . import config, prompts
from .llm import get_provider
from .llm.base import GenerateRequest, ProviderError

IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_TEXT_BYTES = 20 * 1024 * 1024
MAX_DOC_BYTES = 50 * 1024 * 1024


def validate_image(data: bytes) -> str:
    """Return normalized format name; raises ValueError for anything that is not a real PNG/JPEG/WebP."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds 15 MB limit")
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
            fmt = (im.format or "").lower()
    except Exception as e:  # noqa: BLE001
        raise ValueError("File is not a valid image") from e
    if fmt not in ("png", "jpeg", "webp"):
        raise ValueError(f"Unsupported image format {fmt!r}; use PNG, JPEG or WebP")
    return fmt


def ocr_backends() -> list[str]:
    out = []
    s = config.load_settings().models
    if s.vision_model:
        out.append("vision_model")
    if tesseract_available():
        out.append("tesseract")
    return out


def tesseract_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def extract_agenda_text(image: bytes) -> tuple[str, str]:
    """Return (text, backend). Raises ValueError when no local backend is available."""
    s = config.load_settings().models
    errors = []
    if s.vision_model:
        try:
            req = GenerateRequest(
                model=s.vision_model,
                system="You transcribe agenda screenshots exactly.",
                prompt=prompts.OCR_INSTRUCTIONS,
                images=[image],
                temperature=0.0,
                num_ctx=min(s.context_length, 16384),
                max_tokens=s.max_output_tokens,
                purpose="ocr",
            )
            return get_provider().generate(req).strip(), "vision_model"
        except ProviderError as e:
            errors.append(str(e))
    if tesseract_available():
        import pytesseract

        with Image.open(io.BytesIO(image)) as im:
            text = pytesseract.image_to_string(im)
        return text.strip(), "tesseract"
    raise ValueError(
        "No local image-to-text backend is available (set a vision model in Settings or install Tesseract). Enter the agenda manually."
        + (f" Errors: {errors}" if errors else "")
    )


def extract_document_text(filename: str, data: bytes) -> tuple[str, str]:
    """Return (text, kind) for TXT/MD/PDF/DOCX. Everything runs locally."""
    lower = filename.lower()
    if lower.endswith((".txt", ".md", ".markdown")):
        if len(data) > MAX_TEXT_BYTES:
            raise ValueError("Text file too large")
        return data.decode("utf-8", errors="replace"), "md" if lower.endswith((".md", ".markdown")) else "txt"
    if len(data) > MAX_DOC_BYTES:
        raise ValueError("Document too large")
    if lower.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages), "pdf"
    if lower.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        return "\n".join(p.text for p in d.paragraphs), "docx"
    raise ValueError("Unsupported document type; use TXT, Markdown, PDF or DOCX")


def test_inference(model: str) -> dict[str, Any]:
    req = GenerateRequest(
        model=model,
        system="Reply with JSON only.",
        prompt=prompts.TEST_INFERENCE_PROMPT,
        json_schema={
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "echo": {"type": "string"}},
            "required": ["ok", "echo"],
        },
        temperature=0.0,
        num_ctx=4096,
        max_tokens=64,
        purpose="test",
    )
    import time

    t0 = time.time()
    raw = get_provider().generate(req)
    return {
        "model": model,
        "seconds": round(time.time() - t0, 2),
        "raw": raw[:500],
        "ok": '"ok"' in raw,
    }
