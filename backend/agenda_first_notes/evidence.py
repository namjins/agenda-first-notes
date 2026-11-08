"""Evidence validation: a cited quote must exist verbatim (modulo whitespace) in the source text."""

from __future__ import annotations

import re
from typing import Any

HEDGES = re.compile(
    r"\b(maybe|perhaps|might|could|possibly|what if|should we|not sure|unclear|wonder|think about|consider|"
    r"we'll see|let's see|idea|suggest|propose|proposal|if we)\b|\?",
    re.I,
)
COMMITS = re.compile(
    r"\b(decided|decision|agreed|approved|approve|will do|i'll|i will|yes[,.]|assigned|confirmed|"
    r"motion|unanimous|let's go with|we will|accepted|done|completed)\b",
    re.I,
)
CONFIDENT_TEXT = re.compile(r"\b(decided|approved|agreed|assigned|confirmed|accepted|completed|will)\b", re.I)
HEDGED_TEXT = re.compile(
    r"\b(unclear|not confirmed|no final decision|may|might|possibly|indicated|proposed|suggested|"
    r"under consideration|discussion|unresolved|needs verification|whether)\b",
    re.I,
)


def _norm_pattern(quote: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in quote.split()]
    return re.compile(r"\s+".join(parts), re.I)


def locate(quote: str, text: str, offset: int = 0) -> tuple[int, int] | None:
    """Return (start, end) of quote in text as global offsets (text begins at `offset`)."""
    q = quote.strip().strip('"“”')
    if len(q) < 3:
        return None
    i = text.find(q)
    if i == -1:
        m = _norm_pattern(q).search(text)
        if not m:
            return None
        return offset + m.start(), offset + m.end()
    return offset + i, offset + i + len(q)


def validate_quotes(
    quotes: list[str], text: str, offset: int, transcript_version_id: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split quotes into located evidence dicts and rejected quotes."""
    ok: list[dict[str, Any]] = []
    bad: list[str] = []
    for q in quotes:
        loc = locate(q, text, offset)
        if loc is None:
            bad.append(q)
        else:
            s, e = loc
            ok.append(
                {
                    "start": s,
                    "end": e,
                    "quote": text[s - offset : e - offset],
                    "transcript_version_id": transcript_version_id,
                }
            )
    return ok, bad


def revalidate(evidence: list[dict[str, Any]], full_text: str, transcript_version_id: str) -> list[dict[str, Any]]:
    """Re-locate evidence against a (possibly corrected) transcript. Drops quotes that vanished."""
    kept = []
    for ev in evidence:
        s, e = ev.get("start", -1), ev.get("end", -1)
        if 0 <= s < e <= len(full_text) and full_text[s:e] == ev.get("quote"):
            kept.append({**ev, "transcript_version_id": transcript_version_id})
            continue
        loc = locate(ev.get("quote", ""), full_text)
        if loc:
            kept.append(
                {
                    "start": loc[0],
                    "end": loc[1],
                    "quote": full_text[loc[0] : loc[1]],
                    "transcript_version_id": transcript_version_id,
                }
            )
    return kept


def owner_supported(owner: str | None, evidence: list[dict[str, Any]]) -> bool:
    if not owner:
        return True
    tokens = [t for t in re.split(r"\s+", owner.strip()) if t]
    if not tokens:
        return False
    blob = " ".join(e["quote"] for e in evidence).lower()
    return all(t.lower().strip(".,") in blob for t in tokens)


def looks_overconfident(text: str, category: str, uncertainty: str, evidence: list[dict[str, Any]]) -> bool:
    """shortcut: lexical heuristic for certainty mismatch; the model reviewer does the semantic pass."""
    if HEDGED_TEXT.search(text):
        return False
    if uncertainty == "high" and CONFIDENT_TEXT.search(text):
        return True
    if category in ("decision", "assignment", "completion", "calling_event") and evidence:
        quotes = " ".join(e["quote"] for e in evidence)
        if HEDGES.search(quotes) and not COMMITS.search(quotes) and CONFIDENT_TEXT.search(text):
            return True
    return False


def same_claim(a: str, b: str) -> bool:
    na = re.sub(r"[^a-z0-9 ]", "", a.lower()).split()
    nb = re.sub(r"[^a-z0-9 ]", "", b.lower()).split()
    if not na or not nb:
        return False
    if na == nb:
        return True
    inter = len(set(na) & set(nb))
    return inter / max(len(set(na)), len(set(nb))) >= 0.8
