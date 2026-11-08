"""Evidence validation: a cited quote must exist verbatim (modulo whitespace) in the source text."""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
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


_FOLD = str.maketrans(
    {
        **dict.fromkeys("\u2018\u2019\u201a\u201b\u02bc\u2032", "'"),
        **dict.fromkeys("\u201c\u201d\u201e\u201f\u2033", '"'),
        **dict.fromkeys("\u2010\u2011\u2012\u2013\u2014\u2015\u2212", "-"),
    }
)


@lru_cache(maxsize=8)
def _fold(text: str) -> tuple[str, tuple[int, ...]]:
    """NFC + apostrophe/quote/dash folding. Returns the folded text and, per folded char, its source index
    (plus a final entry for len(text)) so matches map back to exact offsets in the original."""
    out: list[str] = []
    src: list[int] = []
    i = 0
    while i < len(text):
        j = i + 1
        while j < len(text) and unicodedata.combining(text[j]):
            j += 1
        piece = unicodedata.normalize("NFC", text[i:j]).translate(_FOLD)
        out.append(piece)
        src.extend([i] * len(piece))
        i = j
    src.append(len(text))
    return "".join(out), tuple(src)


def fold(text: str) -> str:
    return _fold(text)[0]


def _norm_pattern(quote: str) -> re.Pattern[str]:
    parts = [re.escape(p) for p in quote.split()]
    return re.compile(r"\s+".join(parts), re.I)


STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "yes",
    "no",
    "ok",
    "okay",
    "so",
    "well",
    "um",
    "uh",
    "to",
    "of",
    "in",
    "it",
    "is",
    "that",
    "this",
    "we",
    "i",
    # filler: a quote made only of these verifies nothing
    "yeah",
    "yep",
    "yup",
    "mm",
    "hmm",
    "mhm",
    "mm-hmm",
    "uh-huh",
    "huh",
    "oh",
    "ah",
    "er",
    "right",
    "sure",
    "alright",
}

_WORD = re.compile(r"[^\W_]+(?:['-][^\W_]+)*")


def substantive_quote(q: str) -> bool:
    """At least two word tokens and at least one that is not a stopword/filler ("I'll do it." passes, "um yeah" fails)."""
    words = _WORD.findall(fold(q))
    return len(words) >= 2 and any(w.lower() not in STOPWORDS for w in words)


def locate(quote: str, text: str, offset: int = 0) -> tuple[int, int] | None:
    """Return (start, end) of quote in text as global offsets (text begins at `offset`).

    Matching ignores whitespace runs, case, Unicode normalization (NFC) and curly-vs-straight quotes/dashes."""
    q = fold(quote).strip().strip('"')
    if not substantive_quote(q):
        return None
    ft, src = _fold(text)
    i = ft.find(q)
    if i != -1:
        s, e = i, i + len(q)
    else:
        m = _norm_pattern(q).search(ft)
        if not m:
            return None
        s, e = m.start(), m.end()
    # map folded offsets back; an end inside a composed cluster extends to the cluster's end
    k = e
    while k < len(src) - 1 and src[k] == src[e - 1]:
        k += 1
    return offset + src[s], offset + src[k]


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
    """Owners need a full name (>= 2 tokens) appearing contiguously, as whole words, inside one cited quote
    (tokens scattered across quotes or people, e.g. "Sam Alpha and Pat Beta" -> "Sam Beta", do not count)."""
    if not owner:
        return True
    tokens = [t.strip(".,") for t in fold(owner).split() if t.strip(".,")]
    if len(tokens) < 2:
        return False
    pat = re.compile(r"(?<!\w)" + r"\s+".join(map(re.escape, tokens)) + r"(?!\w)", re.I)
    return any(pat.search(fold(e["quote"])) for e in evidence)


def looks_overconfident(text: str, category: str, uncertainty: str, evidence: list[dict[str, Any]]) -> bool:
    """shortcut: lexical heuristic for certainty mismatch; the model reviewer does the semantic pass."""
    if HEDGED_TEXT.search(text):
        return False
    if uncertainty == "high" and CONFIDENT_TEXT.search(text):
        return True
    if evidence:  # any category: confident wording over hedged-only evidence (rule 6)
        quotes = " ".join(e["quote"] for e in evidence)
        if HEDGES.search(quotes) and not COMMITS.search(quotes) and CONFIDENT_TEXT.search(text):
            return True
    return False


NEGATIONS = {"not", "no", "never", "nt", "none", "without", "unclear", "declined"}


def same_claim(a: str, b: str) -> bool:
    na = re.sub(r"[^a-z0-9 ]", "", fold(a).lower().replace("n't", " nt")).split()
    nb = re.sub(r"[^a-z0-9 ]", "", fold(b).lower().replace("n't", " nt")).split()
    if not na or not nb:
        return False
    if na == nb:
        return True
    if (set(na) & NEGATIONS) != (set(nb) & NEGATIONS):
        return False  # "approved" vs "not approved" are different claims
    inter = len(set(na) & set(nb))
    return inter / max(len(set(na)), len(set(nb))) >= 0.9  # near-identical only; distinct objects stay separate
