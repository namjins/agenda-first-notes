"""Evidence-first processing: segmentation -> extraction -> validation -> review to convergence.

Pure functions over data; persistence happens in services.py. Historical records are never
passed in here, by construction.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from . import evidence, prompts
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
        raise PipelineError(f"Model returned invalid JSON: {e.errors()[:1]}") from e


def note_dict(f: Finding, ev: list[dict[str, Any]], status: str) -> dict[str, Any]:
    return {
        "agenda_node_id": f.agenda_node_id,
        "text": f.text.strip(),
        "category": f.category if f.category in NOTE_CATEGORIES else "discussion",
        "uncertainty": f.uncertainty if f.uncertainty in ("low", "medium", "high") else "medium",
        "follow_up": bool(f.follow_up),
        "owner": f.owner.strip() if f.owner else None,
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
            outline=outline(tree) or "(no agenda supplied; use null agenda_node_id)",
            seg_no=seg.index + 1,
            seg_total=seg_total,
            start=seg.start,
            end=seg.end,
            excerpt=excerpt,
        ),
        json_schema=ExtractionOutput.model_json_schema(),
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
        if not f.text.strip():
            continue
        if f.agenda_node_id is not None and f.agenda_node_id not in valid_ids:
            f.agenda_node_id = None
        ev, bad = evidence.validate_quotes(f.evidence, excerpt, seg.start, transcript_version_id)
        status = "verified" if ev else "unsupported"
        if f.uncertainty == "high" and ev:
            status = "uncertain"
        n = note_dict(f, ev, status)
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
            if ev:
                n["status"] = "needs_review"
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


def dedupe(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Overlapping segments produce duplicates; keep the first occurrence of an equivalent claim."""
    kept: list[dict[str, Any]] = []
    for n in notes:
        dup = False
        for k in kept:
            if k["agenda_node_id"] == n["agenda_node_id"] and evidence.same_claim(k["text"], n["text"]):
                dup = True
                if n["follow_up"]:
                    k["follow_up"] = True
                for ev in n["evidence"]:
                    if ev not in k["evidence"]:
                        k["evidence"].append(ev)
                break
        if not dup:
            kept.append(n)
    return kept


def deterministic_checks(notes: list[dict[str, Any]], tree: list[AgendaNode]) -> list[dict[str, Any]]:
    """Checks that must pass before the notes can be called technically converged."""
    valid_ids = node_ids(tree)
    out: list[dict[str, Any]] = []
    for n in notes:
        if n.get("deleted"):
            continue
        nid = n.get("id")
        if n["origin"] == "user":
            if n["status"] == "manual_unverified":
                out.append(
                    _finding(
                        nid,
                        "info",
                        "manual",
                        "Manual addition; not verified against the transcript.",
                        None,
                    )
                )
            continue
        if n["agenda_node_id"] is not None and n["agenda_node_id"] not in valid_ids:
            out.append(
                _finding(
                    nid,
                    "high",
                    "wrong_placement",
                    "Note points at an agenda node that no longer exists.",
                    None,
                )
            )
        if not n["evidence"] and n["status"] != "manual_unverified":
            out.append(_finding(nid, "high", "unsupported", "No evidence attached.", None))
        if n.get("owner") and not evidence.owner_supported(n["owner"], n["evidence"]):
            out.append(_finding(nid, "high", "inferred_owner", "Owner is not named in the cited evidence.", None))
        if n.get("stale"):
            out.append(
                _finding(
                    nid,
                    "high",
                    "stale",
                    "Source transcript changed since this note was verified.",
                    None,
                )
            )
        if evidence.looks_overconfident(n["text"], n["category"], n["uncertainty"], n["evidence"]):
            out.append(
                _finding(
                    nid,
                    "high",
                    "overconfident",
                    "Wording is more certain than the evidence supports.",
                    None,
                )
            )
        if n["agenda_node_id"] is None and tree:
            out.append(
                _finding(
                    nid,
                    "low",
                    "unmapped",
                    "Substantive discussion not mapped to an agenda item.",
                    None,
                )
            )
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
            outline=outline(tree), notes=json.dumps(payload, ensure_ascii=False), excerpt=excerpt
        ),
        json_schema=ReviewOutput.model_json_schema(),
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
            "message": item.message,
            "action": None,
            "source": "model",
            "round_no": round_no,
        }
        n = by_id.get(item.note_id or "")
        if item.action == "add":
            ev, _bad = evidence.validate_quotes(item.evidence, full_text, 0, transcript_version_id)
            if ev and item.replacement_text and (item.new_agenda_node_id in valid_ids or not tree):
                new = note_dict(
                    Finding(
                        agenda_node_id=item.new_agenda_node_id if item.new_agenda_node_id in valid_ids else None,
                        text=item.replacement_text,
                        category="discussion",
                        uncertainty=item.new_uncertainty or "medium",
                        evidence=[],
                    ),
                    ev,
                    "verified",
                )
                new["position"] = len(notes)
                notes.append(new)
                rec["action"] = "added"
                material += 1  # a newly discovered omission means another round is needed
            else:
                rec["action"] = "add_rejected_no_evidence"
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
        elif item.action == "rewrite" and item.replacement_text:
            n["text"] = item.replacement_text.strip()
            if item.new_uncertainty in ("low", "medium", "high"):
                n["uncertainty"] = item.new_uncertainty
            n["status"] = "uncertain" if n["uncertainty"] == "high" else "verified"
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


def propose_agenda(provider: ModelProvider, settings: ModelSettings, workspace_id: str, text: str) -> list[AgendaNode]:
    req = GenerateRequest(
        model=settings.primary_model or "",
        system=prompts.system_prompt(workspace_id),
        prompt=prompts.AGENDA_PROPOSAL_INSTRUCTIONS.format(excerpt=text[: settings.segment_chars]),
        json_schema=AgendaProposal.model_json_schema(),
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
