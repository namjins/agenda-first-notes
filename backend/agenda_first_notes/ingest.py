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
MAX_DOC_EXPANDED_BYTES = 200 * 1024 * 1024  # decompressed ceiling for DOCX parts / PDF page content (zip bombs)
MAX_PDF_PAGES = 2000


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
        with Image.open(io.BytesIO(image)) as im:
            text = _tesseract_indented(im)
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
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError("PDF has too many pages")
        parts, expanded = [], 0
        for page in reader.pages:
            # shortcut: counts page content streams only; form XObjects rely on pypdf's per-stream output caps.
            contents = page.get_contents()
            expanded += len(contents.get_data()) if contents is not None else 0
            if expanded > MAX_DOC_EXPANDED_BYTES:
                raise ValueError("PDF content too large when decompressed")
            parts.append(page.extract_text() or "")
        return "\n\n".join(parts), "pdf"
    if lower.endswith(".docx"):
        import zipfile

        import docx

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                expanded = sum(i.file_size for i in z.infolist())  # zipfile never inflates past file_size
        except zipfile.BadZipFile as e:
            raise ValueError("File is not a valid DOCX document") from e
        if expanded > MAX_DOC_EXPANDED_BYTES:
            raise ValueError("DOCX content too large when decompressed")
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


def _tesseract_indented(im: Image.Image) -> str:
    """OCR that keeps nesting: indent level is inferred from each line's left x-offset.

    shortcut: assumes a left-aligned list where deeper items are shifted right by a roughly constant step;
    centered or multi-column layouts come out flat and the user fixes them in the tree editor.
    """
    import pytesseract

    data = pytesseract.image_to_data(im, output_type=pytesseract.Output.DICT)
    lines: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((data["left"][i], word))
    rows = [(min(x for x, _ in ws), " ".join(w for _, w in sorted(ws))) for ws in lines.values()]
    rows.sort(key=lambda r: _line_top(data, r[1]))
    if not rows:
        return ""
    lefts = sorted({x for x, _ in rows})
    base = lefts[0]
    steps = [b - a for a, b in zip(lefts, lefts[1:], strict=False) if b - a >= 12]
    unit = min(steps) if steps else 0
    out = []
    for x, text in rows:
        level = round((x - base) / unit) if unit else 0
        out.append("  " * level + text)
    return "\n".join(out)


def _line_top(data: dict[str, Any], text: str) -> int:
    first = text.split(" ", 1)[0]
    for i, w in enumerate(data["text"]):
        if w == first:
            return int(data["top"][i])
    return 0
