"""Split long transcripts into overlapping segments with original offsets. Never drops text."""

from __future__ import annotations

from pydantic import BaseModel


class Segment(BaseModel):
    index: int
    start: int
    end: int
    status: str = "pending"  # pending | done | failed | cancelled
    error: str | None = None


def segment(text: str, max_chars: int, overlap: int = 0) -> list[Segment]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    overlap = min(overlap, max_chars // 2)
    n = len(text)
    if n == 0:
        return []
    segs: list[Segment] = []
    start = 0
    while True:
        end = min(n, start + max_chars)
        if end < n:
            # Prefer breaking at a paragraph, then line, then sentence boundary in the back half.
            window = text[start + max_chars // 2 : end]
            for sep in ("\n\n", "\n", ". "):
                cut = window.rfind(sep)
                if cut != -1:
                    end = start + max_chars // 2 + cut + len(sep)
                    break
        segs.append(Segment(index=len(segs), start=start, end=end))
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    assert segs[0].start == 0 and segs[-1].end == n
    return segs


def coverage_complete(segs: list[Segment]) -> bool:
    return bool(segs) and all(s.status == "done" for s in segs)
