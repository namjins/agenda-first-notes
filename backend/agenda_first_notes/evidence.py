"""Evidence validation: a cited quote must exist verbatim (modulo whitespace) in the source text."""

from __future__ import annotations

import bisect
import re
import unicodedata
from functools import lru_cache
from typing import Any

HEDGES = re.compile(
    r"\b(maybe|perhaps|might|could|possibly|what if|should we|not sure|unclear|wonder|think about|consider|"
    r"we'll see|let's see|idea|suggest|propose|proposal|if we)\b|\?",
    re.I,
)
# An answer or commitment in the quotes outweighs a hedge or a question ("would you? Sure, happy to").
COMMITS = re.compile(
    r"\b(decided|decision|agreed|approved|approve|will do|i'll|i will|yes|yeah|yep|assigned|confirmed|"
    r"motion|unanimous|let's go with|we will|accepted|done|completed|happy to|absolutely|sounds good|sure|"
    r"i can do (?:it|that)|we can do (?:it|that)|i got it|count me in|take care of it|let's do|okay,? let's|"
    r"it is[.!])(?!\w)",
    re.I,
)
# An answer only counts when its own clause (from the commit phrase to the sentence end) is not hedged, it is not
# negated ("not really sure") and it is not inside a question ("is it done?").
_CLAUSE_HEDGE = re.compile(
    r"\b(maybe|perhaps|might|possibly|probably|think about|not sure|not really|we'll see|let's see|"
    r"i don't know|don't know)\b",
    re.I,
)
_NEGATED = re.compile(r"\b(?:not|never|n't)(?:\s+\w+)?\s*$", re.I)


def committed(quotes: list[str]) -> bool:
    """True when some quote holds an unhedged, unnegated answer or commitment outside a question."""
    for q in quotes:
        for sentence in re.split(r"(?<=[.!?])\s+|\n", fold(q)):
            if sentence.rstrip().endswith("?"):
                continue
            for m in COMMITS.finditer(sentence):
                if not _CLAUSE_HEDGE.search(sentence[m.start() :]) and not _NEGATED.search(sentence[: m.start()]):
                    return True
    return False


CONFIDENT_TEXT = re.compile(r"\b(decided|approved|agreed|assigned|confirmed|accepted|completed|will)\b", re.I)
HEDGED_TEXT = re.compile(
    # lowercase-only "may": the modal, not the month; bare "discussion" is no hedge ("Following discussion, approved")
    r"\b(unclear|not confirmed|no final decision|(?-i:may)|might|possibly|indicated|proposed|suggested|"
    r"under consideration|unresolved|needs verification|whether)\b",
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


# Crosstalk: between quoted fragments a transcript may hold speaker labels ("Speaker 2:" anywhere; "Pat:" or
# "Sam Alpha:" only at a turn start, i.e. after a newline, dash or bracket tag), bracket tags ("[crosstalk]") and
# dash/ellipsis punctuation. Only these are skipped, never a word of speech.
_GAP = (
    r"(?:\s|-|\.\.\.|\u2026|\[[^\]\n]{1,40}\]"
    r"|(?-i:Speaker\s*\d+):|(?:(?<=[-\]\n])|(?<=[-\]\n]\s))(?-i:[A-Z][^\W\d_]*(?:\s+[A-Z][^\W\d_]*){0,2}):(?=\s))+"
)


def _crosstalk_pattern(quote: str) -> str:
    parts = [p.strip("-.\u2026") or p for p in re.split(r"\s+|(?<=\w)-{2,}|\.\.\.|\u2026", quote)]
    return _GAP.join(re.escape(p) for p in parts if re.search(r"\w", p))


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


def locate(quote: str, text: str, offset: int = 0, window: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """Return (start, end) of quote in text as global offsets (text begins at `offset`).

    Matching ignores whitespace runs, case, Unicode normalization (NFC) and curly-vs-straight quotes/dashes.
    With `window` (start, end in text), the match must lie inside it, but word boundaries are still judged
    against the whole text, so a segment that starts mid-word cannot yield a fragment ("dan Lee" of "Jordan Lee")."""
    q = fold(quote).strip().strip('"')
    if not substantive_quote(q):
        return None
    ft, src = _fold(text)
    pos = bisect.bisect_left(src, window[0]) if window else 0
    head, tail = (r"(?<!\w)" if re.match(r"\w", q) else ""), (r"(?!\w)" if re.search(r"\w$", q) else "")
    m = None
    for body in (_norm_pattern(q).pattern, _crosstalk_pattern(q)):  # verbatim first, crosstalk only as fallback
        m = re.compile(head + body + tail, re.I).search(ft, pos)  # lookbehind still sees text before pos
        if m:
            break
    if not m:
        return None
    s, e = m.start(), m.end()
    # map folded offsets back; an end inside a composed cluster extends to the cluster's end
    k = e
    while k < len(src) - 1 and src[k] == src[e - 1]:
        k += 1
    if window and src[k] > window[1]:
        return None
    return offset + src[s], offset + src[k]


def validate_quotes(
    quotes: list[str],
    text: str,
    offset: int,
    transcript_version_id: str,
    window: tuple[int, int] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split quotes into located evidence dicts and rejected quotes (see locate for `window`)."""
    ok: list[dict[str, Any]] = []
    bad: list[str] = []
    for q in quotes:
        spans = _locate_all(q, text, offset, window)
        if not spans:
            bad.append(q)
        for s, e in spans:
            ok.append(
                {
                    "start": s,
                    "end": e,
                    "quote": text[s - offset : e - offset],
                    "context": context(text, s - offset, e - offset),
                    "transcript_version_id": transcript_version_id,
                }
            )
    return ok, bad


def _locate_all(quote: str, text: str, offset: int, window: tuple[int, int] | None) -> list[tuple[int, int]]:
    """The quote's span; or, for a quote stitched from non-adjacent fragments with "..." / "…", one span per
    fragment, each located in order inside the window (every fragment must be found, or none counts)."""
    loc = locate(quote, text, offset, window)
    if loc is not None:
        return [loc]
    parts = [p for p in re.split(r"\.\.\.|\u2026", fold(quote)) if p.strip()]
    if len(parts) < 2:
        return []
    lo, hi = window or (0, len(text))
    spans: list[tuple[int, int]] = []
    for p in parts:
        found = locate(p, text, offset, (lo, hi))
        if found is None:
            return []
        spans.append(found)
        lo = found[1] - offset
    return spans


def context(text: str, s: int, e: int, limit: int = 300) -> str:
    """The transcript line (speaker turn) around a quote, at most `limit` characters either side: where a name
    in a note may come from (rule 7) without being part of the quote itself."""
    a = max(text.rfind("\n", 0, s) + 1, s - limit)
    b = text.find("\n", e)
    return text[a : min(b if b != -1 else len(text), e + limit)]


def revalidate(evidence: list[dict[str, Any]], full_text: str, transcript_version_id: str) -> list[dict[str, Any]]:
    """Re-locate evidence against a (possibly corrected) transcript. Drops quotes that vanished."""
    kept = []
    for ev in evidence:
        s, e = ev.get("start", -1), ev.get("end", -1)
        if 0 <= s < e <= len(full_text) and full_text[s:e] == ev.get("quote"):
            kept.append({**ev, "context": context(full_text, s, e), "transcript_version_id": transcript_version_id})
            continue
        loc = locate(ev.get("quote", ""), full_text)
        if loc:
            kept.append(
                {
                    "start": loc[0],
                    "end": loc[1],
                    "quote": full_text[loc[0] : loc[1]],
                    "context": context(full_text, loc[0], loc[1]),
                    "transcript_version_id": transcript_version_id,
                }
            )
    return kept


# Not a full name: a title plus a surname ("Sister Doe", "The Bishop") or a group/role ("Relief Society").
# Only the first word (titles) and the last word (group/role nouns) are tested, so surnames like Ward or Young pass.
TITLES = {"the", "a", "an", "bishop", "sister", "brother", "elder", "president", "counselor", "mr", "mrs", "ms", "dr"}
TITLES |= {"bro", "sis", "pres", "our", "my", "his", "her", "their"}
GROUP_NOUNS = {"society", "quorum", "council", "presidency", "bishopric", "committee", "class", "school", "women"}
GROUP_NOUNS |= {"men", "youth", "days", "conference", "president", "leader", "leaders", "teacher", "secretary"}


_UNITS = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen".split()
_UNITS += "seventeen eighteen nineteen".split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_ORD = "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth".split()
_ORD += "fifteenth sixteenth seventeenth eighteenth nineteenth".split()
NUMBER_WORDS: dict[str, int] = {w: i for i, w in enumerate(_UNITS)} | {w: i + 1 for i, w in enumerate(_ORD)} | _TENS
NUMBER_WORDS |= {"twentieth": 20, "thirtieth": 30, "noon": 12, "midday": 12, "midnight": 12, "half": 30}
NUMBER_WORDS |= {"quarter": 15, "dozen": 12, "hundred": 100, "thousand": 1000}
_NUM = re.compile(r"\d+|[a-z]+", re.I)


def numbers_in(text: str, note_side: bool = False) -> set[int]:
    """Every number a text states, as digits ("10", "21st", "10:30"; ":00" minutes say nothing) or words ("ten",
    "twenty-first" -> 20 and 21, "noon" -> 12). On the note side "one"/"first" are pronoun-like and skipped."""
    t = re.sub(r":00\b", "", fold(text).lower())
    out: set[int] = set()
    prev_tens = 0
    for m in _NUM.finditer(t):
        w = m.group()
        n = int(w) if w.isdigit() else NUMBER_WORDS.get(w)
        if n is not None and prev_tens and 0 < n < 10 and not w.isdigit():
            out.discard(prev_tens)
            out.add(prev_tens + n)  # "twenty-one", "twenty-first"
        elif n is not None and not (note_side and w in ("one", "first")):
            out.add(n)
        prev_tens = _TENS.get(w, 0)
        if w == "quarter":
            out.add(45)
    return out


def unsupported_numbers(text: str, evidence: list[dict[str, Any]]) -> list[int]:
    """Numbers and times in a note that its cited quotes (or their transcript lines) never state (rule 3)."""
    said = set().union(*(numbers_in(e[k]) for e in evidence for k in ("quote", "context") if e.get(k)))
    return sorted(numbers_in(text, note_side=True) - said)


def phrase_in(phrase: str, text: str) -> bool:
    """Whole-word, case/whitespace/Unicode-insensitive containment."""
    return phrase_count(phrase, text) > 0


def phrase_count(phrase: str, text: str) -> int:
    """Whole-word, case/whitespace/Unicode-insensitive occurrences of `phrase` in `text`."""
    tokens = fold(phrase).split()
    if not tokens:
        return 0
    return len(re.findall(r"(?<!\w)" + r"\s+".join(map(re.escape, tokens)) + r"(?!\w)", fold(text), re.I))


def is_full_name(name: str) -> bool:
    tokens = [t.strip(".,").lower() for t in fold(name).split() if t.strip(".,")]
    return len(tokens) >= 2 and tokens[0] not in TITLES and tokens[-1] not in GROUP_NOUNS


def owner_supported(owner: str | None, evidence: list[dict[str, Any]]) -> bool:
    """Owners need a full name (>= 2 tokens, not a title/role/organisation) appearing contiguously, as whole words,
    inside one cited quote (tokens scattered across quotes or people, e.g. "Sam Alpha and Pat Beta" -> "Sam Beta",
    do not count)."""
    if not owner:
        return True
    name = " ".join(t.strip(".,") for t in owner.split() if t.strip(".,"))
    return is_full_name(name) and any(phrase_in(name, e["quote"]) for e in evidence)


def looks_overconfident(text: str, category: str, uncertainty: str, evidence: list[dict[str, Any]]) -> bool:
    """shortcut: lexical heuristic for certainty mismatch; the model reviewer does the semantic pass."""
    if HEDGED_TEXT.search(text):
        return False
    if uncertainty == "high" and CONFIDENT_TEXT.search(text):
        return True
    if evidence:  # any category: confident wording over hedged or unanswered evidence (rule 6)
        quotes = [e["quote"] for e in evidence]
        if HEDGES.search("\n".join(quotes)) and not committed(quotes) and CONFIDENT_TEXT.search(text):
            return True
    return False


NEGATIONS = {"not", "no", "never", "nt", "none", "without", "unclear", "declined", "cannot", "unable", "nobody"}
NEGATIONS |= {"nothing", "neither", "nor", "nowhere", "refused", "rejected", "denied"}


_FILLER_WORDS = {"will", "shall", "to", "a", "an", "the"}


def _stem(w: str) -> str:
    """Crude inflection stem (repairs/repaired/repairing -> repair); numbers are never stemmed."""
    if w.isdigit() or w in STOPWORDS:
        return w
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            w = w[: -len(suf)]
            break
    return w[:-1] if w.endswith("e") and len(w) > 3 else w


def same_claim(a: str, b: str) -> bool:
    """shortcut: compares sets of content-word stems, so word order and tense are ignored ("Sam will thank Pat" ==
    "Pat thanked Sam"); upgrade to an order-aware comparison if role-swapped claims ever need to stay apart."""
    na = re.sub(r"[^a-z0-9 ]", "", fold(a).lower().replace("n't", " nt")).split()
    nb = re.sub(r"[^a-z0-9 ]", "", fold(b).lower().replace("n't", " nt")).split()
    if not na or not nb:
        return False
    if na == nb:
        return True
    if (set(na) & NEGATIONS) != (set(nb) & NEGATIONS):
        return False  # "approved" vs "not approved" are different claims
    # Same content words (inflections folded) = same claim; articles and will/to/shall carry no claim. A differing
    # name, place, number or object is a different claim.
    ca = {_stem(w) for w in na} - STOPWORDS - _FILLER_WORDS
    cb = {_stem(w) for w in nb} - STOPWORDS - _FILLER_WORDS
    return bool(ca) and ca == cb


def _content(text: str) -> tuple[set[str], set[str], set[str]]:
    """Content-word stems, negations and number words of a text."""
    low = re.sub(r"[^a-z0-9 ]", " ", fold(text).lower().replace("n't", " nt")).split()
    stems = {_stem(w) for w in low} - STOPWORDS - _FILLER_WORDS
    return stems, set(low) & NEGATIONS, {w for w in low if any(c.isdigit() for c in w)}


def similar_claim(a: str, b: str, quotes_a: list[str], quotes_b: list[str]) -> bool:
    """The same finding worded twice (overlapping segments, repeated extraction passes): same_claim, or at least
    7 in 10 of the shorter text's content words are in the other, with the same negations and numbers, unless EACH
    text has a word of its own that comes from its evidence quotes. Such a word is a fact from the transcript (a
    name, place, object), so "at the church" and "at the park" stay apart; a word of its own that is not in the
    quotes is only the model's wording ("a participant suggested" / "the speaker proposed").

    shortcut: lexical; threshold chosen on real passes. Only for merging fresh model notes; comparisons with
    user-touched notes keep the strict same_claim."""
    if same_claim(a, b):
        return True
    (sa, na, da), (sb, nb, db) = _content(a), _content(b)
    if min(len(sa), len(sb)) < 3 or na != nb or da != db or len(sa & sb) / min(len(sa), len(sb)) < 0.7:
        return False
    return not ((sa - sb) & _content(" ".join(quotes_a))[0] and (sb - sa) & _content(" ".join(quotes_b))[0])
