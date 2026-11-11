"""Evidence-first processing: segmentation -> extraction -> validation -> review to convergence.

Pure functions over data; persistence happens in services.py. Historical records are never
passed in here, by construction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import evidence, prompts, render
from .agenda import AgendaNode, node_ids, outline, walk
from .config import ModelSettings
from .llm.base import GenerateRequest, ModelProvider
from .models import CALLING_EVENTS, NOTE_CATEGORIES
from .segmentation import Segment, segment


class Finding(BaseModel):
    agenda_node_id: str | None = None
    text: str
    category: str = "discussion"
    uncertainty: str = "low"
    follow_up: bool = False
    owner: str | None = None
    calling_event: str | None = None
    evidence: list[str] = Field(default_factory=list)


class DiscussedItem(BaseModel):
    agenda_node_id: str
    evidence: list[str] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    discussed: list[DiscussedItem] = Field(default_factory=list)


class ReviewItem(BaseModel):
    note_id: str | None = None
    severity: str = "low"
    category: str = "unsupported"
    message: str = ""
    action: str = "keep"
    replacement_text: str | None = None
    new_agenda_node_id: str | None = None
    new_uncertainty: str | None = None
    follow_up: bool = False  # for action=add
    evidence: list[str] = Field(default_factory=list)


class ReviewOutput(BaseModel):
    findings: list[ReviewItem] = Field(default_factory=list)


class TopicProposal(BaseModel):
    text: str
    children: list[TopicProposal] = Field(default_factory=list)


class AgendaProposal(BaseModel):
    topics: list[TopicProposal] = Field(default_factory=list)


class PipelineError(RuntimeError):
    pass


# The user's confirmed names for this meeting: (heard text, name forms, agenda headings naming that person). A whole
# confirmed name form counts as stated only in a note citing a quote that contains its heard text (see
# unsupported_names); nothing else about rule 7 changes. The headings only mark where such a note may belong.
Confirmed = Sequence[tuple[str, Sequence[str], Sequence[str]]]


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with every property required, so constrained decoding makes the model emit all fields
    (Pydantic marks fields with defaults as optional, and models then silently omit agenda_node_id etc.)."""
    schema = model.model_json_schema()

    def harden(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
            for v in node.values():
                harden(v)
        elif isinstance(node, list):
            for v in node:
                harden(v)

    harden(schema)
    return schema


def _parse(model: type[BaseModel], raw: str) -> Any:
    try:
        return model.model_validate_json(raw)
    except ValidationError as e:
        # Some models wrap JSON in prose or fences; take the outermost object.
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                return model.model_validate_json(raw[start : end + 1])
            except ValidationError:
                pass
        raise PipelineError(f"Model returned invalid JSON: {e.errors(include_input=False)[:1]}") from e


def note_dict(f: Finding, ev: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "agenda_node_id": f.agenda_node_id,
        "text": render.clean_text(f.text),
        "category": f.category if f.category in NOTE_CATEGORIES else "discussion",
        "uncertainty": f.uncertainty if f.uncertainty in ("low", "medium", "high") else "medium",
        "follow_up": bool(f.follow_up),
        "owner": " ".join(f.owner.split()) or None if f.owner else None,
        "calling_event": f.calling_event if f.calling_event in CALLING_EVENTS else None,
        "evidence": ev,
        "status": status,
        "origin": "model",
        "stale": False,
        "deleted": False,
        "position": 0,
        "name_confirmed": False,
    }


def extract_segment(
    provider: ModelProvider,
    settings: ModelSettings,
    workspace_id: str,
    tree: list[AgendaNode],
    full_text: str,
    seg: Segment,
    seg_total: int,
    transcript_version_id: str,
    confirmed: Confirmed = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (notes, deterministic findings) for one segment."""
    excerpt = full_text[seg.start : seg.end]
    req = GenerateRequest(
        model=settings.primary_model or "",
        system=prompts.system_prompt(workspace_id),
        prompt=prompts.EXTRACT_INSTRUCTIONS.format(
            outline=prompts.fence(outline(tree)) or "(no agenda supplied; use null agenda_node_id)",
            seg_no=seg.index + 1,
            seg_total=seg_total,
            start=seg.start,
            end=seg.end,
            excerpt=prompts.fence(excerpt),
            names=prompts.confirmed_names_block(confirmed),
        ),
        json_schema=strict_schema(ExtractionOutput),
        temperature=settings.temperature,
        num_ctx=settings.context_length,
        max_tokens=settings.max_output_tokens,
        purpose="extract",
        think=settings.think,
    )
    out: ExtractionOutput = _parse(ExtractionOutput, provider.generate(req))
    valid_ids = node_ids(tree)
    notes: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for f in out.findings:
        if not render.clean_text(f.text):
            continue
        if f.agenda_node_id is not None and f.agenda_node_id not in valid_ids:
            f.agenda_node_id = None
        ev, bad = evidence.validate_quotes(f.evidence, full_text, 0, transcript_version_id, (seg.start, seg.end))
        n = note_dict(f, ev, "unsupported")
        n["status"] = derive_status(n, full_text, confirmed)
        own: list[dict[str, Any]] = []  # findings about this note; service attaches note ids after insert
        if bad:
            own.append(
                _finding(
                    None,
                    "low" if ev else "high",
                    "unsupported",
                    f"Quote not found in transcript: {bad[0][:80]!r}",
                    "evidence_rejected",
                )
            )
        if not ev:
            own.append(
                _finding(
                    None,
                    "high",
                    "unsupported",
                    f"No verifiable evidence for: {n['text'][:100]!r}",
                    "excluded",
                )
            )
        if n["owner"] and (n["category"] != "assignment" or not evidence.owner_supported(n["owner"], ev)):
            own.append(
                _finding(
                    None,
                    "low",
                    "inferred_owner",
                    f"Owner {n['owner']!r} not explicitly supported by evidence; removed.",
                    "strip_owner",
                )
            )
            n["owner"] = None
        if evidence.looks_overconfident(n["text"], n["category"], n["uncertainty"], ev):
            own.append(
                _finding(
                    None,
                    "high",
                    "overconfident",
                    f"Wording more certain than evidence supports: {n['text'][:100]!r}",
                    None,
                )
            )
        if f.agenda_node_id is None and tree and ev:
            own.append(
                _finding(
                    None,
                    "low",
                    "unmapped",
                    f"Substantive discussion did not map to an agenda item: {n['text'][:100]!r}",
                    None,
                )
            )
        n["_findings"] = own
        findings.extend(own)
        notes.append(n)
    for d in out.discussed:
        # Discussed without a clear outcome (rule 5 fallback); only with located evidence, never as absence.
        if d.agenda_node_id in valid_ids:
            ev, _bad = evidence.validate_quotes(d.evidence, full_text, 0, transcript_version_id, (seg.start, seg.end))
            if ev:
                notes.append(marker_note(d.agenda_node_id, ev))
    return notes, findings


def marker_note(agenda_node_id: str | None, ev: list[dict[str, Any]]) -> dict[str, Any]:
    """The "Discussion occurred; no clear decisions or action items identified." note for a discussed item."""
    n = note_dict(Finding(agenda_node_id=agenda_node_id, text=render.NO_CLEAR_OUTCOME), list(ev), "unsupported")
    n["status"] = derive_status(n)
    n["_findings"] = []
    return n


def _finding(note_id: str | None, severity: str, category: str, message: str, action: str | None) -> dict[str, Any]:
    return {
        "note_id": note_id,
        "severity": severity,
        "category": category,
        "message": message,
        "action": action,
        "source": "deterministic",
    }


def derive_status(n: dict[str, Any], transcript: str = "", confirmed: Confirmed = ()) -> str:
    """Model-note status from its evidence alone; 'verified' only when every deterministic check passes. Also sets
    n["name_confirmed"]: its names pass only thanks to a user-confirmed name. Stored with the note (and its approved
    snapshot) so the label and the history origin never depend on confirmations that may change later."""
    n["name_confirmed"] = confirmed_name_used(n["text"], n["evidence"], transcript, confirmed)
    if not n["evidence"]:
        return "unsupported"
    if evidence.looks_overconfident(n["text"], n["category"], n["uncertainty"], n["evidence"]):
        return "needs_review"
    if unsupported_facts(n["text"], n["evidence"], transcript, confirmed):
        return "needs_review"
    return "uncertain" if n["uncertainty"] == "high" else "verified"


# Most cautious first: when duplicates disagree, the merged note keeps the weaker claim (rule 6).
CAUTION = [
    "uncertainty",
    "open_question",
    "discussion",
    "proposed_decision",
    "follow_up_candidate",
    "clarification",
    "dependency",
    "date",
    "status_change",
    "calling_event",
    "assignment",
    "decision",
    "completion",
]
_LEVELS = ("low", "medium", "high")


def _merge(k: dict[str, Any], n: dict[str, Any], transcript: str = "", confirmed: Confirmed = ()) -> None:
    """Fold duplicate n into k: weaker category/wording, higher uncertainty, union of evidence, re-derived status."""
    if CAUTION.index(n["category"]) < CAUTION.index(k["category"]):
        k["text"], k["category"] = n["text"], n["category"]
    k["uncertainty"] = max(k["uncertainty"], n["uncertainty"], key=_LEVELS.index)
    k["calling_event"] = k["calling_event"] or n["calling_event"]
    k["follow_up"] = k["follow_up"] or n["follow_up"]
    owners = {o for o in (k["owner"], n["owner"]) if o}
    k["owner"] = owners.pop() if len(owners) == 1 and k["category"] == "assignment" else None
    for ev in n["evidence"]:
        if ev not in k["evidence"]:
            k["evidence"].append(ev)
    if k["owner"] and not evidence.owner_supported(k["owner"], k["evidence"]):
        k["owner"] = None
    k["status"] = derive_status(k, transcript, confirmed)
    k.setdefault("_findings", []).extend(n.get("_findings", []))  # so the duplicate's findings get a note id
    if k["evidence"]:
        for fd in k["_findings"]:
            if fd.get("action") in ("excluded", "evidence_rejected"):
                fd["severity"] = "info"  # superseded: the merged claim has located evidence


def dedupe(notes: list[dict[str, Any]], transcript: str = "", confirmed: Confirmed = ()) -> list[dict[str, Any]]:
    """Overlapping segments produce duplicates; merge equivalent claims on the same agenda item.

    Distinct calling events are never merged (rule 8)."""
    kept: list[dict[str, Any]] = []
    for n in notes:
        for k in kept:
            ce = {k["calling_event"], n["calling_event"]} - {None}
            if (
                k["agenda_node_id"] == n["agenda_node_id"]
                and len(ce) <= 1
                and evidence.same_claim(k["text"], n["text"])
            ):
                _merge(k, n, transcript, confirmed)
                break
        else:
            kept.append(n)
    return kept  # discussion markers are kept: render decides whether the fallback line shows


def claim_exists(
    agenda_node_id: str | None,
    text: str,
    notes: list[dict[str, Any]],
    tree: list[AgendaNode] | None = None,
    headings: dict[str, str] | None = None,
) -> bool:
    """True when a note (deleted and user-touched included) already makes this claim on this agenda item.

    Notes with status 'unsupported' are ignored: they are hidden and never rendered. A note's current text on its
    current node and its 'source_text' on its 'source_node_id' (before a user rewording or move) both count. Same
    rule as services' regeneration suppression: with `tree`, a stored node no longer in it compares by heading text
    (`headings`: node id -> normalised heading, over every agenda revision); an unknown node (None, or not in
    `headings`) matches any item, erring towards "already exists"."""
    live = node_ids(tree) if tree is not None else None

    def same_item(stored: str | None) -> bool:
        if live is None or stored in live:
            return stored == agenda_node_id
        if not headings or stored not in headings:
            return True
        return headings[stored] == headings.get(agenda_node_id or "")

    for n in notes:
        if n.get("status") == "unsupported":
            continue
        for node, t in ((n["agenda_node_id"], n["text"]), (n.get("source_node_id"), n.get("source_text"))):
            if t and same_item(node) and evidence.same_claim(t, text):
                return True
    return False


_WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
_ABBREV = {"sis", "bro", "dr", "st", "mr", "mrs", "ms", "jr", "sr"}  # "Sis. Reyes", "Sam J. Alpha": no sentence end
# Note-writing words the model uses that a transcript may never say in lowercase. Closed: not names, not places.
NOTE_WORDS = (
    evidence.STOPWORDS | evidence.TITLES | _ABBREV | {"decided", "decision", "agreed", "approved", "discussion"}
)
NOTE_WORDS |= {"discussed", "unclear", "pending", "follow-up", "follow", "assigned", "reported", "noted", "not", "none"}
NOTE_WORDS |= {"proposed", "suggested", "confirmed", "completed", "whether", "next", "meeting", "he", "she", "they"}
NOTE_WORDS |= {"you", "its", "all", "each", "both", "someone", "nobody", "nothing", "as", "at", "by", "for", "from"}
NOTE_WORDS |= {"on", "with", "if", "when", "after", "before", "until", "there", "then", "also", "participant"}
# imperative note verbs ("Ask Pat...", "Invite Jordan..."); words that are common given names are deliberately absent
NOTE_WORDS |= {"ask", "invite", "contact", "confirm", "schedule", "plan", "prepare", "send", "call", "check", "review"}
NOTE_WORDS |= {"consider", "coordinate", "arrange", "book", "order", "remind", "share", "update", "finalize", "submit"}
NOTE_WORDS |= {"visit", "extend", "release", "discuss", "reach", "provide", "organize", "identify", "determine"}
NOTE_WORDS |= {"bring", "continue", "revisit", "report", "look", "find", "get", "set", "assign", "verify"}
NOTE_WORDS |= {"requested", "encouraged", "ensure", "recruit", "possible", "potential", "needed", "planned"}
NOTE_WORDS |= {"scheduled", "expected", "required", "offered", "accepted", "declined", "postponed", "deferred"}
NOTE_WORDS |= {"mentioned", "raised", "recommended", "ongoing", "remaining", "additional", "tentative", "likely"}
# Church-organisation and calendar vocabulary: transcripts garble it ("steak", "elders corum", "release society"),
# notes spell it properly. Never a personal name on its own; right after a title it is checked as a name (below).
NOTE_WORDS |= {"stake", "ward", "branch", "relief", "society", "quorum", "elders", "priesthood", "aaronic"}
NOTE_WORDS |= {"melchizedek", "primary", "young", "women", "men", "youth", "bishop", "bishopric", "presidency"}
NOTE_WORDS |= {"sacrament", "temple", "seminary", "institute", "ministering", "sunday", "school", "council"}
NOTE_WORDS |= {"conference", "fireside", "gospel", "foundations", "monday", "tuesday", "wednesday", "thursday"}
NOTE_WORDS |= {"friday", "saturday", "january", "february", "march", "april", "may", "june", "july", "august"}
NOTE_WORDS |= {"september", "october", "november", "december"}
NOTE_WORDS |= {"christmas", "easter", "thanksgiving", "halloween", "am", "pm", "church", "christ", "jesus"}
NOTE_WORDS |= {"mission", "missionary", "missionaries", "fsy", "activity", "activities"}
# After one of these, the next capitalised word is a name ("Brother Young"): never ordinary, never closed vocabulary.
_NAME_TITLES = {"brother", "sister", "bishop", "elder", "president", "pres", "sis", "bro", "mr", "mrs", "ms", "dr"}


def _caps(text: str) -> tuple[list[tuple[str, bool]], list[list[tuple[str, bool]]]]:
    """Capitalised words of folded text as (word, follows_a_title), and maximal runs of adjacent ones (separated by
    whitespace, or by ". " after an initial or a title abbreviation)."""
    t = evidence.fold(text)
    tokens: list[tuple[str, bool]] = []
    runs: list[list[tuple[str, bool]]] = []
    prev_end, prev = -1, ""
    for m in _WORD.finditer(t):
        w = re.sub(r"'s$", "", m.group())  # possessive: "Sam Alpha's" names Sam Alpha
        if w[0].isupper():
            gap = t[prev_end : m.start()]
            abbrev = len(prev) == 1 or prev.lower() in _ABBREV
            joined = bool(prev) and (gap.isspace() or (abbrev and gap.startswith(".") and gap[1:].isspace()))
            tok = (w, joined and prev.lower() in _NAME_TITLES)
            if joined:
                runs[-1].append(tok)
            else:
                runs.append([tok])
            tokens.append(tok)
            prev = w
        else:
            prev = ""
        prev_end = m.end()
    return tokens, runs


def _in_evidence(
    words: list[str], ev: list[dict[str, Any]], keys: tuple[str, ...] = ("quote", "context"), any_case: bool = True
) -> bool:
    """Contiguous (initials' dots optional), any case unless told otherwise, inside one quote or its line."""
    pat = re.compile(r"(?<!\w)" + r"\.?\s+".join(map(re.escape, words)) + r"(?!\w)", re.I if any_case else 0)
    return any(pat.search(evidence.fold(e[k])) for e in ev for k in keys if e.get(k))


@lru_cache(maxsize=4)
def _lower_words(text: str) -> frozenset[str]:
    """Lowercase words of a text, and their inflection stems ("requests" -> "request")."""
    words = {w for w in _WORD.findall(evidence.fold(text)) if w[0].islower()}
    return frozenset(words | {evidence._stem(w) for w in words})


def unsupported_names(
    text: str, ev: list[dict[str, Any]], transcript: str = "", confirmed: Confirmed = ()
) -> list[str]:
    """Rules 3 and 7, one rule for every capitalised word of a note, sentence start included. It is fine when it
    (a) occurs in a cited quote or the transcript line around it, (b) occurs in lowercase somewhere in the
    transcript or the evidence, compared by inflection stem (an ordinary word: "Ask", "Requested", "Move-in"), or
    (c) is closed note-writing, church-organisation or calendar vocabulary ("Decided", "Quorum", "Thursday").
    Anything else is a proper noun taken from elsewhere in the transcript, from the agenda (never evidence) or
    invented. A word right after a title ("Brother Young") is always a name: only (a) counts. Adjacent capitalised
    words containing such a proper noun must also occur together in one quote or its line (no "Sam" + "Alpha"
    assembled from two places); leading ordinary words ("Contact Sam Alpha") are dropped from that check, trailing
    ones ("Sam Ward") are not. A reported name that equals a whole form (full or preferred) the user confirmed for a
    heard text passes when a cited quote (never only its line: rule 12) contains that heard text (`confirmed`);
    a part of it ("Ross", "Sister Ross") does not. The label "name confirmed by you" comes from confirmed_name_used."""
    lower = _lower_words(transcript).union(*(_lower_words(e[k]) for e in ev for k in ("quote", "context") if e.get(k)))

    def ordinary(tok: tuple[str, bool]) -> bool:
        w, titled = tok[0].lower(), tok[1]
        if titled:
            return False
        if w in NOTE_WORDS or evidence._stem(w) in NOTE_WORDS or w in lower or evidence._stem(w) in lower:
            return True
        parts = w.split("-")
        return len(parts) > 1 and all(p in NOTE_WORDS or p in lower or evidence._stem(p) in lower for p in parts)

    ok = _confirmed_forms(ev, confirmed, transcript) if confirmed else set()
    tokens, runs = _caps(text)
    bad_runs = []
    for r in runs:
        # "Robert Ross" stays whole when confirmed, even if "robert" also occurs lowercase in the transcript
        while r and ordinary(r[0]) and _name_key(" ".join(w for w, _ in r)) not in ok:
            r = r[1:]
        if len(r) >= 2 and not all(map(ordinary, r)) and not _in_evidence([w for w, _ in r], ev):
            bad_runs.append(r)
    reported = {tok for r in bad_runs for tok in r}
    # a titled word must occur as a name (capitalised) in the quote or its line: "the young men" is no "Brother Young"
    lone = [
        t for t in tokens if t not in reported and not ordinary(t) and not _in_evidence([t[0]], ev, any_case=not t[1])
    ]
    out = list(dict.fromkeys([" ".join(w for w, _ in r) for r in bad_runs] + [w for w, _ in lone]))
    return [x for x in out if _name_key(x) not in ok]


def _name_key(name: str) -> str:
    return " ".join(evidence.fold(name).lower().split())


@lru_cache(maxsize=64)
def bare_mentions(heard: str, text: str, context: str = "") -> tuple[tuple[int, int], ...]:
    """Spans (offsets in `text`) where `heard` occurs as a mention of its own, not as part of a longer one: not right
    after a title ("Sister Ross") or another name-like word ("Robert Ross"), not right before a name-like word. A
    confirmation of "Ross" covers only these. Name-like: capitalised, not note vocabulary, never lowercase in `text`
    or `context` (the transcript, when `text` is a quote)."""
    tokens = evidence.fold(heard).split()
    if not tokens:
        return ()
    t, src = evidence._fold(text)
    lower, more = _lower_words(text), _lower_words(context)

    def nameish(w: str) -> bool:
        lw, st = w.lower(), evidence._stem(w.lower())
        return w[0].isupper() and lw not in NOTE_WORDS and not ({lw, st} & lower or {lw, st} & more)

    out = []
    for m in re.finditer(r"(?<!\w)" + r"\s+".join(map(re.escape, tokens)) + r"(?!\w)", t, re.I):
        before = re.search(r"(?<!\w)(" + _WORD.pattern + r")(\.?)\s+$", t[max(0, m.start() - 40) : m.start()])
        if before:
            w, dot = before.group(1), before.group(2)
            if (not dot or len(w) == 1 or w.lower() in _ABBREV) and (w.lower() in _NAME_TITLES or nameish(w)):
                continue
        after = re.match(r"\s+(" + _WORD.pattern + ")", t[m.end() : m.end() + 40])
        if after and nameish(after.group(1)):
            continue
        out.append((src[m.start()], src[m.end()]))
    return tuple(out)


def _confirmed_said(ev: list[dict[str, Any]], confirmed: Confirmed, transcript: str = "") -> Confirmed:
    """The confirmed names whose heard text a cited quote contains as a mention of its own (bare_mentions), judged
    in the transcript around the quote when the quote's offsets point at it."""

    def said(heard: str, e: dict[str, Any]) -> bool:
        s, end = e.get("start", -1), e.get("end", -1)
        if transcript and 0 <= s < end <= len(transcript) and transcript[s:end] == e["quote"]:
            return any(s <= a and b <= end for a, b in bare_mentions(heard, transcript))
        return bool(bare_mentions(heard, e["quote"], transcript))

    quotes = [e for e in ev if e.get("quote")]
    return [c for c in confirmed if any(said(c[0], e) for e in quotes)]


def _confirmed_forms(ev: list[dict[str, Any]], confirmed: Confirmed, transcript: str = "") -> set[str]:
    """Whole confirmed name forms whose heard text a cited quote contains (see _confirmed_said)."""
    return {_name_key(f) for _, forms, _ in _confirmed_said(ev, confirmed, transcript) for f in forms}


def confirmed_name_used(text: str, ev: list[dict[str, Any]], transcript: str, confirmed: Confirmed) -> bool:
    """True when the note's names pass the check only thanks to a user-confirmed name (shown as "name confirmed by
    you", never as source-verified; history rows from it are the user's, not the transcript's)."""
    return bool(confirmed) and unsupported_names(text, ev, transcript) != unsupported_names(
        text, ev, transcript, confirmed
    )


def unsupported_facts(
    text: str, ev: list[dict[str, Any]], transcript: str = "", confirmed: Confirmed = ()
) -> list[str]:
    """Names (see unsupported_names) and numbers or times (evidence.unsupported_numbers) the evidence never states."""
    return unsupported_names(text, ev, transcript, confirmed) + [str(x) for x in evidence.unsupported_numbers(text, ev)]


_LEAD_IN = 250  # characters of transcript before a quote that may name the topic it continues ("Youth conference: ...")


def _stems(text: str) -> set[str]:
    words = {p for w in _WORD.findall(evidence.fold(text).lower()) for p in [w, *w.split("-")]}
    return {evidence._stem(w) for w in words} - evidence.STOPWORDS


def _topic_words(heading: str, transcript: str) -> set[str]:
    """Content words of an agenda heading, stem-folded, without stopwords and person names (capitalised words
    the transcript never uses in lowercase): "Dale Pruett - Elders Quorum President" -> elder, quorum, president."""
    lower = _lower_words(transcript)
    names = {
        w.lower()
        for w in _WORD.findall(evidence.fold(heading))
        if w[0].isupper() and not ({w.lower(), evidence._stem(w.lower())} & (lower | NOTE_WORDS))
    }
    return _stems(heading) - {evidence._stem(x) for x in names}


def placement_findings(
    n: dict[str, Any], tree: list[AgendaNode], transcript: str, confirmed: Confirmed = ()
) -> list[dict[str, Any]]:
    """Is the note (or discussion marker) about the agenda item it sits under? Relevance must show in the transcript,
    not in the model's own words: some content word of the item's heading or its ancestors' headings has to occur
    in a cited quote, its line, or the lead-in just before the quote. Otherwise: "May not belong under" - high for
    markers (a false "discussed" is a rule-1 claim) and for notes on a leaf, low on a group heading. A group-level
    note that shares words with one child heading gets a low "May belong under" instead. A cited quote saying a confirmed name's heard text ("sharon lomy")
    only lowers that to low for a note under an agenda heading naming that person ("Sharon Lomy - Primary"): a name
    is no topic. Never for a marker: a name mention alone is no discussion (rule 1). Needs the transcript."""
    path: dict[str, list[AgendaNode]] = {}
    stack: list[AgendaNode] = []
    for x, depth in walk(tree):
        stack[depth:] = [x]
        path[x.id] = list(stack)
    nodes = path.get(n["agenda_node_id"] or "")
    if not transcript or not nodes or n.get("source_node_id") not in (None, n["agenda_node_id"]):
        return []  # no transcript to judge by, unmapped, or placed there by the user
    node, nid, marker = nodes[-1], n.get("id"), render.is_marker(n)
    seen = " ".join(
        f"{e.get('context', '')} {transcript[max(0, e['start'] - _LEAD_IN) : e['end']]}"
        for e in n["evidence"]
        if 0 <= e.get("start", -1) < e.get("end", -1) <= len(transcript)
    ) + " ".join(e["quote"] for e in n["evidence"])
    topic = set().union(*(_topic_words(x.text, transcript) for x in nodes))
    named = {h for _, _, headings in _confirmed_said(n["evidence"], confirmed, transcript) for h in headings}
    if topic and not topic & _stems(seen):
        linked = not marker and any(x.text in named for x in nodes)
        sev = "high" if (marker or not node.children) and not linked else "low"
        return [_finding(nid, sev, "wrong_placement", f"May not belong under '{node.text[:80]}'.", "move")]
    if node.children and not marker:
        said = _stems(n["text"] + " " + " ".join(e["quote"] for e in n["evidence"]))
        for c in node.children:
            if (_topic_words(c.text, transcript) - topic) & said:
                return [_finding(nid, "low", "wrong_placement", f"May belong under '{c.text[:80]}'.", "move")]
    return []


def deterministic_checks(
    notes: list[dict[str, Any]], tree: list[AgendaNode], transcript: str = "", confirmed: Confirmed = ()
) -> list[dict[str, Any]]:
    """Checks that must pass before the notes can be called technically converged. Pass the current transcript:
    without it the name check cannot tell ordinary words from proper nouns and errs towards flagging."""
    valid_ids = node_ids(tree)
    out: list[dict[str, Any]] = []
    for n in notes:
        if n.get("deleted"):
            continue
        nid = n.get("id")
        if n["origin"] != "user" and n["status"] == "unsupported":
            # Excluded from the notes and flagged once at extraction; never material, not even when unplaced.
            out.append(_finding(nid, "low", "unsupported", "Excluded from the notes: no verifiable evidence.", None))
            continue
        # Structural checks apply to every note, including user-written ones.
        if n["agenda_node_id"] is not None and n["agenda_node_id"] not in valid_ids:
            out.append(
                _finding(nid, "high", "wrong_placement", "Note points at an agenda node that no longer exists.", None)
            )
        if n["agenda_node_id"] is None and tree:
            sev = "high" if n.get("follow_up") else "low"
            out.append(
                _finding(
                    nid, sev, "unmapped", "Not placed under an agenda item; it will not be rendered until it is.", None
                )
            )
        if n.get("stale"):
            out.append(
                _finding(
                    nid, "high", "stale", "Source transcript or agenda changed since this note was verified.", None
                )
            )
        if n["origin"] == "user":
            if n["status"] == "manual_unverified":
                out.append(
                    _finding(nid, "info", "manual", "Manual addition; not verified against the transcript.", None)
                )
            continue
        if not n["evidence"]:
            out.append(_finding(nid, "high", "unsupported", "No evidence attached.", None))
        if n.get("owner") and not evidence.owner_supported(n["owner"], n["evidence"]):
            out.append(
                _finding(nid, "high", "inferred_owner", "Owner is not a full name present in the cited evidence.", None)
            )
        if evidence.looks_overconfident(n["text"], n["category"], n["uncertainty"], n["evidence"]):
            out.append(
                _finding(nid, "high", "overconfident", "Wording is more certain than the evidence supports.", None)
            )
        names = unsupported_names(n["text"], n["evidence"], transcript, confirmed)
        if names:  # rules 3/7: a proper noun the cited evidence does not contain blocks
            out.append(_finding(nid, "high", "name", f"Not in the cited evidence: {', '.join(names)[:200]}.", None))
        nums = evidence.unsupported_numbers(n["text"], n["evidence"])
        if nums:  # rule 3: a number, date or time the evidence never states blocks
            msg = f"Number or time not in the cited evidence: {', '.join(map(str, nums))}."
            out.append(_finding(nid, "high", "number", msg, None))
        out.extend(placement_findings(n, tree, transcript, confirmed))
    return out


def review_round(
    provider: ModelProvider,
    settings: ModelSettings,
    workspace_id: str,
    tree: list[AgendaNode],
    notes: list[dict[str, Any]],
    full_text: str,
    transcript_version_id: str,
    round_no: int,
    headings: dict[str, str] | None = None,
    confirmed: Confirmed = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Apply one adversarial review round. Returns (notes, findings, number_of_material_findings).

    Only model-origin notes are changed automatically; user edits are preserved. `headings` (node id ->
    normalised heading text over every agenda revision) lets detached user notes be matched by heading; see
    claim_exists.
    """
    if not settings.review_by_segment or len(full_text) <= settings.segment_chars:
        excerpt = (
            full_text
            if len(full_text) <= settings.segment_chars
            else full_text[: settings.segment_chars]
            + "\n[...excerpt truncated for review; omission checks limited to this portion]"
        )
        return _review_part(
            provider, settings, workspace_id, tree, notes, None, excerpt, full_text,
            transcript_version_id, round_no, headings, confirmed,
        )  # fmt: skip
    # One request per extraction segment: the reviewer reads every part for omissions, and each note is checked
    # once, in the part holding its first evidence quote (no evidence: the first part).
    segs = segment(full_text, settings.segment_chars, settings.segment_overlap_chars)
    home = {
        id(n): next((s.index for s in segs if n["evidence"] and s.start <= n["evidence"][0]["start"] < s.end), 0)
        for n in notes
    }
    findings: list[dict[str, Any]] = []
    material = 0
    for s in segs:
        excerpt = (
            f"[Part {s.index + 1} of {len(segs)} of the transcript (characters {s.start}-{s.end}). The draft notes "
            "shown are those whose evidence is in this part; the other parts are reviewed separately.]\n"
            + full_text[s.start : s.end]
        )
        notes, f, m = _review_part(
            provider, settings, workspace_id, tree, notes, {k for k, v in home.items() if v == s.index}, excerpt,
            full_text, transcript_version_id, round_no, headings, confirmed,
        )  # fmt: skip
        findings += f
        material += m
    return notes, findings, material


def _review_part(
    provider: ModelProvider,
    settings: ModelSettings,
    workspace_id: str,
    tree: list[AgendaNode],
    notes: list[dict[str, Any]],
    shown: set[int] | None,
    excerpt: str,
    full_text: str,
    transcript_version_id: str,
    round_no: int,
    headings: dict[str, str] | None,
    confirmed: Confirmed,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """One review request over `excerpt`, showing the notes whose id() is in `shown` (None: all), and its findings applied."""
    # Discussion markers are evidence that an item was discussed, not notes: the reviewer never sees or edits them.
    live = [n for n in notes if not n.get("deleted") and n["status"] != "unsupported" and not render.is_marker(n)]
    live = [n for n in live if shown is None or id(n) in shown]
    payload = [
        {
            k: n[k]
            for k in (
                "id",
                "agenda_node_id",
                "text",
                "category",
                "uncertainty",
                "follow_up",
                "owner",
                "calling_event",
            )
        }
        | {"evidence": [e["quote"] for e in n["evidence"]], "origin": n["origin"]}
        for n in live
    ]
    req = GenerateRequest(
        model=settings.reviewer_model or settings.primary_model or "",
        system=prompts.system_prompt(workspace_id),
        prompt=prompts.REVIEW_INSTRUCTIONS.format(
            outline=prompts.fence(outline(tree)),
            notes=prompts.fence(json.dumps(payload, ensure_ascii=False)),
            excerpt=prompts.fence(excerpt),
            names=prompts.confirmed_names_block(confirmed),
        ),
        json_schema=strict_schema(ReviewOutput),
        temperature=settings.temperature,
        num_ctx=settings.context_length,
        max_tokens=settings.max_output_tokens,
        purpose="review",
        think=settings.think,
    )
    out: ReviewOutput = _parse(ReviewOutput, provider.generate(req))
    by_id = {n["id"]: n for n in notes if "id" in n}
    valid_ids = node_ids(tree)
    findings: list[dict[str, Any]] = []
    material = 0
    for item in out.findings:
        rec = {
            "note_id": item.note_id,
            "severity": item.severity if item.severity in ("low", "high") else "low",
            "category": item.category,
            "message": item.message.strip()[:600],
            "action": None,
            "source": "model",
            "round_no": round_no,
        }
        n = by_id.get(item.note_id or "")
        if item.action == "add":
            ev, _bad = evidence.validate_quotes(item.evidence, full_text, 0, transcript_version_id)
            node = item.new_agenda_node_id if item.new_agenda_node_id in valid_ids else None
            text = render.clean_text(item.replacement_text or "")
            if not (ev and text and (node or not tree)):
                rec["action"] = "add_rejected_no_evidence"
            elif claim_exists(node, text, notes, tree, headings):
                # Already present, or deleted/edited by the user (or removed earlier): never resurrect it.
                rec["action"] = "add_rejected_duplicate"
            elif unsupported_names(text, ev, full_text, confirmed):
                rec["action"] = "add_rejected_unsupported_name"
            elif evidence.unsupported_numbers(text, ev):
                rec["action"] = "add_rejected_unsupported_number"
            else:
                new = note_dict(
                    Finding(
                        agenda_node_id=node,
                        text=text,
                        category="discussion",
                        uncertainty=item.new_uncertainty or "medium",
                        follow_up=item.follow_up,
                        evidence=[],
                    ),
                    ev,
                    "unsupported",
                )
                new["status"] = derive_status(new, full_text, confirmed)
                new["position"] = len(notes)
                notes.append(new)
                rec["action"] = "added"
                material += 1  # a newly discovered omission means another round is needed
            findings.append(rec)
            continue
        if n is not None and _disputes_present_name(item, n):
            # The evidence quotes were already verified verbatim in the transcript; a reviewer claiming a
            # name in them is "not in the transcript" is itself hallucinating. Record, do not apply.
            rec["action"] = "rejected_name_is_in_cited_evidence"
            rec["severity"] = "info"
            findings.append(rec)
            continue
        if n is None or n["origin"] == "user" or item.action == "keep" or render.is_marker(n):
            rec["action"] = "none"
            findings.append(rec)
            if rec["severity"] == "high" and n is not None and n["origin"] == "user":
                material += 1
            continue
        if item.action == "remove":
            n["deleted"] = True
            rec["action"] = "removed"
            # its verified quotes still mark the item as discussed at render time (never "Not discussed.")
        elif item.action == "rewrite" and render.clean_text(item.replacement_text or ""):
            text = render.clean_text(item.replacement_text or "")
            bad = (
                "name"
                if unsupported_names(text, n["evidence"], full_text, confirmed)
                else "number"
                if evidence.unsupported_numbers(text, n["evidence"])
                else None
            )
            if bad:  # a rewrite may not introduce a name, number or time its evidence does not state
                rec["action"] = f"rewrite_rejected_unsupported_{bad}"
                if rec["severity"] == "high":
                    material += 1
            else:
                n["text"] = text
                # A rewrite may hedge, never firm up: uncertainty only moves towards "high" (rule 6).
                if item.new_uncertainty in _LEVELS and _LEVELS.index(item.new_uncertainty) > _LEVELS.index(
                    n["uncertainty"]
                ):
                    n["uncertainty"] = item.new_uncertainty
                n["status"] = derive_status(n, full_text, confirmed)
                rec["action"] = "rewritten"
        elif item.action == "move" and item.new_agenda_node_id in valid_ids:
            n["agenda_node_id"] = item.new_agenda_node_id
            rec["action"] = "moved"
        elif item.action == "strip_owner":
            n["owner"] = None
            rec["action"] = "owner_removed"
        elif item.action == "clear_follow_up":
            n["follow_up"] = False
            rec["action"] = "follow_up_cleared"
        else:
            rec["action"] = "none"
            if rec["severity"] == "high":
                material += 1
        findings.append(rec)
    return notes, findings, material


# "<name> is not in / does not appear in / is missing from the transcript|excerpt|evidence|quote"
_ABSENCE = re.compile(
    r"(?:\bnot\s+(?:(?:present|found|mentioned|named|stated|included)\s+)?in|\b(?:does|do)(?:n't|\s+not)\s+appear\s+in"
    r"|\babsent\s+from|\bmissing\s+from|\bnowhere\s+in|\bno\s+mention\s+of\b[^.;]*?\bin)"
    r"\s+(?:the\s+)?(?:cited\s+|provided\s+)?(?:transcript|excerpt|evidence|quotes?|source)\b",
    re.I,
)
# Any objection about what someone did, agreed to or owns is never overridden, whatever else the message says.
_CONDUCT = re.compile(
    r"\b(?:accept|agree|assign|commit|volunteer|confirm|decid|approv|respons|request|asked|said yes|owner|own|"
    r"handl|lead|task|charge)",
    re.I,
)


def _disputes_present_name(item: ReviewItem, note: dict[str, Any]) -> bool:
    """True only for a 'contamination'/'name' finding whose sole claim is that ONE specific name is absent from the
    transcript, while that name occurs verbatim in the note's verified quotes. Ownership, acceptance or handling,
    a second person, or any other capitalised word in the message: the objection stands (when in doubt, it stands)."""
    if item.category not in ("contamination", "name") or item.action not in ("rewrite", "remove"):
        return False
    if not _ABSENCE.search(item.message) or _CONDUCT.search(item.message):
        return False
    tokens, runs_t = _caps(item.message)
    names = [w for w, _ in tokens if w.lower() not in NOTE_WORDS]
    runs = [r for r in ([w for w, _ in r if w.lower() not in NOTE_WORDS] for r in runs_t) if r]
    if len(runs) != 1 or len(runs[0]) < 2 or any(w not in runs[0] for w in names):
        return False
    return _in_evidence(runs[0], note["evidence"], ("quote",))  # verbatim in the quotes, not merely nearby


def propose_agenda(provider: ModelProvider, settings: ModelSettings, workspace_id: str, text: str) -> list[AgendaNode]:
    """Headings from the whole transcript: one proposal per segment (a long meeting's later topics count too),
    exact-duplicate top-level headings dropped. The user reviews and edits them before approving (rule 2)."""

    def conv(t: TopicProposal) -> AgendaNode:
        kids = [conv(c) for c in t.children if c.text.strip()]
        return AgendaNode(text=t.text.strip(), kind="group" if kids else "topic", children=kids)

    out: list[AgendaNode] = []
    seen: set[str] = set()
    for seg in segment(text, settings.segment_chars, settings.segment_overlap_chars) or [
        Segment(index=0, start=0, end=0)
    ]:
        req = GenerateRequest(
            model=settings.primary_model or "",
            system=prompts.system_prompt(workspace_id),
            prompt=prompts.AGENDA_PROPOSAL_INSTRUCTIONS.format(excerpt=prompts.fence(text[seg.start : seg.end])),
            json_schema=strict_schema(AgendaProposal),
            temperature=settings.temperature,
            num_ctx=settings.context_length,
            max_tokens=settings.max_output_tokens,
            purpose="agenda",
            think=settings.think,
        )
        proposal: AgendaProposal = _parse(AgendaProposal, provider.generate(req))
        # shortcut: only identical headings merge across segments; reworded repeats stay for the user to merge.
        for t in proposal.topics:
            key = " ".join(evidence.fold(t.text).lower().split())
            if key and key not in seen:
                seen.add(key)
                out.append(conv(t))
    return out


ProgressFn = Callable[[float, str], None]
