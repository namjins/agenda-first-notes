"""Evidence-first processing: segmentation -> extraction -> validation -> review to convergence.

Pure functions over data; persistence happens in services.py. Historical records are never
passed in here, by construction.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import evidence, prompts, render
from .agenda import AgendaNode, node_ids, outline
from .config import ModelSettings
from .llm.base import GenerateRequest, ModelProvider
from .models import CALLING_EVENTS, NOTE_CATEGORIES
from .segmentation import Segment


class Finding(BaseModel):
    agenda_node_id: str | None = None
    text: str
    category: str = "discussion"
    uncertainty: str = "low"
    follow_up: bool = False
    owner: str | None = None
    calling_event: str | None = None
    evidence: list[str] = Field(default_factory=list)


class ExtractionOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


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
        ),
        json_schema=strict_schema(ExtractionOutput),
        temperature=settings.temperature,
        num_ctx=settings.context_length,
        max_tokens=settings.max_output_tokens,
        purpose="extract",
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
        ev, bad = evidence.validate_quotes(f.evidence, excerpt, seg.start, transcript_version_id)
        n = note_dict(f, ev, "unsupported")
        n["status"] = derive_status(n)
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
    return notes, findings


def _finding(note_id: str | None, severity: str, category: str, message: str, action: str | None) -> dict[str, Any]:
    return {
        "note_id": note_id,
        "severity": severity,
        "category": category,
        "message": message,
        "action": action,
        "source": "deterministic",
    }


def derive_status(n: dict[str, Any]) -> str:
    """Model-note status from its evidence alone; 'verified' only when every deterministic check passes."""
    if not n["evidence"]:
        return "unsupported"
    if evidence.looks_overconfident(n["text"], n["category"], n["uncertainty"], n["evidence"]):
        return "needs_review"
    if unsupported_names(n["text"], n["evidence"]):
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


def _merge(k: dict[str, Any], n: dict[str, Any]) -> None:
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
    k["status"] = derive_status(k)
    k.setdefault("_findings", []).extend(n.get("_findings", []))  # so the duplicate's findings get a note id
    if k["evidence"]:
        for fd in k["_findings"]:
            if fd.get("action") in ("excluded", "evidence_rejected"):
                fd["severity"] = "info"  # superseded: the merged claim has located evidence


def dedupe(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
                _merge(k, n)
                break
        else:
            kept.append(n)
    return kept


def claim_exists(agenda_node_id: str | None, text: str, notes: list[dict[str, Any]]) -> bool:
    """True when any note (deleted and user-touched included) already makes this claim on this agenda item."""
    return any(n["agenda_node_id"] == agenda_node_id and evidence.same_claim(n["text"], text) for n in notes)


_NAME = re.compile(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)+\b")


def unsupported_names(text: str, ev: list[dict[str, Any]]) -> list[str]:
    """Capitalised multi-word names in text that do not occur in the cited evidence (rule 7)."""
    blob = evidence.fold(" ".join(e["quote"] for e in ev)).lower()
    return [m for m in _NAME.findall(evidence.fold(text)) if " ".join(m.split()).lower() not in " ".join(blob.split())]


def deterministic_checks(notes: list[dict[str, Any]], tree: list[AgendaNode]) -> list[dict[str, Any]]:
    """Checks that must pass before the notes can be called technically converged."""
    valid_ids = node_ids(tree)
    out: list[dict[str, Any]] = []
    for n in notes:
        if n.get("deleted"):
            continue
        nid = n.get("id")
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
        names = unsupported_names(n["text"], n["evidence"])
        if names:
            out.append(_finding(nid, "low", "name", f"Not in the cited evidence: {', '.join(names)[:200]}.", None))
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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Apply one adversarial review round. Returns (notes, findings, number_of_material_findings).

    Only model-origin notes are changed automatically; user edits are preserved.
    """
    live = [n for n in notes if not n.get("deleted") and n["status"] != "unsupported"]
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
    excerpt = (
        full_text
        if len(full_text) <= settings.segment_chars
        else full_text[: settings.segment_chars]
        + "\n[...excerpt truncated for review; omission checks limited to this portion]"
    )
    req = GenerateRequest(
        model=settings.reviewer_model or settings.primary_model or "",
        system=prompts.system_prompt(workspace_id),
        prompt=prompts.REVIEW_INSTRUCTIONS.format(
            outline=prompts.fence(outline(tree)),
            notes=prompts.fence(json.dumps(payload, ensure_ascii=False)),
            excerpt=prompts.fence(excerpt),
        ),
        json_schema=strict_schema(ReviewOutput),
        temperature=settings.temperature,
        num_ctx=settings.context_length,
        max_tokens=settings.max_output_tokens,
        purpose="review",
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
            elif claim_exists(node, text, notes):
                # Already present, or deleted/edited by the user (or removed earlier): never resurrect it.
                rec["action"] = "add_rejected_duplicate"
            elif unsupported_names(text, ev):
                rec["action"] = "add_rejected_unsupported_name"
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
                new["status"] = derive_status(new)
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
        if n is None or n["origin"] == "user" or item.action == "keep":
            rec["action"] = "none"
            findings.append(rec)
            if rec["severity"] == "high" and n is not None and n["origin"] == "user":
                material += 1
            continue
        if item.action == "remove":
            n["deleted"] = True
            rec["action"] = "removed"
        elif item.action == "rewrite" and render.clean_text(item.replacement_text or ""):
            text = render.clean_text(item.replacement_text or "")
            if unsupported_names(text, n["evidence"]):
                rec["action"] = "rewrite_rejected_unsupported_name"
                if rec["severity"] == "high":
                    material += 1
            else:
                n["text"] = text
                # A rewrite may hedge, never firm up: uncertainty only moves towards "high" (rule 6).
                if item.new_uncertainty in _LEVELS and _LEVELS.index(item.new_uncertainty) > _LEVELS.index(
                    n["uncertainty"]
                ):
                    n["uncertainty"] = item.new_uncertainty
                n["status"] = derive_status(n)
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


_ABSENCE = re.compile(
    r"\b(?:not (?:in|present|found|mentioned|stated|named|appear)|(?:does|do)(?:n't| not) appear|no mention|"
    r"absent|missing from|nowhere)\b",
    re.I,
)


def _disputes_present_name(item: ReviewItem, note: dict[str, Any]) -> bool:
    """Only a claim that a name is ABSENT from the transcript is overridden by verified evidence containing it;
    any other objection (e.g. "X only asked; nothing was decided") stands."""
    if not _ABSENCE.search(item.message):
        return False
    if item.category not in ("contamination", "name", "unsupported") or item.action not in (
        "rewrite",
        "remove",
        "strip_owner",
    ):
        return False
    names = set(_NAME.findall(item.message))
    if not names:
        return False
    blob = " ".join(e["quote"] for e in note["evidence"])
    return all(name in blob for name in names)


def propose_agenda(provider: ModelProvider, settings: ModelSettings, workspace_id: str, text: str) -> list[AgendaNode]:
    req = GenerateRequest(
        model=settings.primary_model or "",
        system=prompts.system_prompt(workspace_id),
        prompt=prompts.AGENDA_PROPOSAL_INSTRUCTIONS.format(excerpt=prompts.fence(text[: settings.segment_chars])),
        json_schema=strict_schema(AgendaProposal),
        temperature=settings.temperature,
        num_ctx=settings.context_length,
        max_tokens=settings.max_output_tokens,
        purpose="agenda",
    )
    out: AgendaProposal = _parse(AgendaProposal, provider.generate(req))

    def conv(t: TopicProposal) -> AgendaNode:
        kids = [conv(c) for c in t.children if c.text.strip()]
        return AgendaNode(text=t.text.strip(), kind="group" if kids else "topic", children=kids)

    return [conv(t) for t in out.topics if t.text.strip()]


ProgressFn = Callable[[float, str], None]
