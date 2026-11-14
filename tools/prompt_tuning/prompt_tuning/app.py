"""Runs the APP'S OWN processing path (services.process_meeting -> pipeline.extract_segment -> evidence validation ->
deterministic checks -> render) with candidate extraction instructions, against local Ollama only."""

from __future__ import annotations

import contextlib
import importlib.util
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agenda_first_notes import config, db, pipeline, prompts, services
from agenda_first_notes.agenda import AgendaNode, from_dicts
from agenda_first_notes.llm.base import GenerateRequest, ModelProvider, ProviderError
from agenda_first_notes.llm.ollama_provider import OllamaProvider
from agenda_first_notes.models import Note, ReviewFinding
from agenda_first_notes.workspaces import WORKSPACES, Workspace, get_workspace, reset_engines, session_for
from sqlalchemy import select

from .metric import HIGH_CATEGORIES, Meeting, norm

# The tuned component is the instruction text after the transcript block; the agenda/transcript framing before it
# stays exactly as in prompts.py, so the placeholders can never be lost.
_SPLIT = "TRANSCRIPT>>>\n"
PREFIX, SEED = prompts.EXTRACT_INSTRUCTIONS.split(_SPLIT, 1)
PREFIX += _SPLIT
BASELINE = prompts.EXTRACT_INSTRUCTIONS


def privacy() -> None:
    """Before anything else: no metadata fetches, no telemetry, no dspy cache. The tool never builds a dspy/litellm
    LM (every model call goes through the app's OllamaProvider), so these are belt and braces."""
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"  # dspy.clients.model_metadata would fetch it from GitHub
    os.environ["LITELLM_TELEMETRY"] = "False"
    import dspy

    dspy.configure_cache(enable_disk_cache=False, enable_memory_cache=False)
    if importlib.util.find_spec("litellm") is not None:  # excluded in pyproject; guard if it ever comes back
        import litellm

        litellm.telemetry = False


def template(tail: str) -> str:
    """Full EXTRACT_INSTRUCTIONS value for a tuned tail (braces escaped for str.format)."""
    return PREFIX + tail.replace("{", "{{").replace("}", "}}")


def check_template(t: str) -> str:
    for key in ("{outline}", "{excerpt}"):
        if key not in t:
            raise ValueError(f"instructions must contain {key}; pass the full EXTRACT_INSTRUCTIONS text")
    t.format(outline="", names="", seg_no=1, seg_total=1, start=0, end=0, excerpt="")  # raises on a stray brace
    return t


@dataclass
class Run:
    markdown: str
    tree: list[AgendaNode]
    high: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    usable: set[int] = field(default_factory=set)  # gold names confirmed, or said in full (see metric.score)


@dataclass
class NameStats:
    """Suggestion accuracy for one meeting's gold names (reported, not tuned). Exact names are never suggested."""

    total: int = 0
    exact: int = 0  # heard text already is the member's name
    suggested: int = 0  # gold member among the candidates for the heard text
    top: int = 0  # gold member is the first candidate


class App:
    """One throwaway data directory (outside the repo) for the whole run, deleted on close."""

    def __init__(
        self,
        model: str,
        host: str,
        review_rounds: int,
        max_output_tokens: int = 12288,
        think: bool = False,
        review_by_segment: bool = False,
        segment_chars: int = 24000,
        context_length: int = 32768,
        filing_step: bool = True,
        extraction_passes: int = 1,
        provider: ModelProvider | None = None,  # tests pass the app's FakeProvider
    ) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="afn-tune-")
        os.environ["AFN_DATA_DIR"] = self._tmp.name
        config.assert_outside_repo(Path(self._tmp.name))
        reset_engines()
        s = config.load_settings()
        s.models.ollama_host, s.models.primary_model, s.models.max_review_rounds = host, model, review_rounds
        s.models.max_output_tokens = max_output_tokens
        s.models.think = think
        s.models.review_by_segment = review_by_segment
        s.models.segment_chars = segment_chars
        s.models.context_length = context_length
        s.models.filing_step = filing_step
        s.models.extraction_passes = extraction_passes
        config.save_settings(s)
        self.model = model
        # OllamaProvider refuses non-loopback hosts; think=False handling lives there
        self.provider: ModelProvider = provider or OllamaProvider(host)
        for wid in WORKSPACES:
            db.migrate(get_workspace(wid))
        self._list: Path | None = None  # member list now in the temp data dir's shared store
        self.name_stats: dict[str, NameStats] = {}  # meeting name -> latest run's suggestion accuracy

    def close(self) -> None:
        reset_engines()
        self._tmp.cleanup()

    def _use_list(self, m: Meeting) -> None:
        """The shared member store holds this meeting's list (none: empty, as in an app without one). Replaced only
        when the list changes; it lives in the throwaway data dir, never in the user's app data."""
        if m.members_path != self._list:
            services.import_members(m.members)
            self._list = m.members_path

    def _confirm_names(self, ws: Workspace, meeting_id: str, m: Meeting) -> set[int]:
        """The simulated user: for each gold name whose heard text is suggested with the gold member as a
        candidate, confirm that member; confirm nothing else. Returns the gold names the notes may use."""
        st, usable = NameStats(total=len(m.gold.names)), set()
        sugs = {norm(x["heard"]): x for x in services.name_suggestions(ws, meeting_id)["suggestions"]}
        full = {x["id"]: x["full_name"] for x in services.list_members()}
        for i, g in enumerate(m.gold.names):
            if g.exact:
                st.exact += 1
                usable.add(i)
                continue
            sug = sugs.get(norm(g.heard))
            cands = [c["member_id"] for c in sug["candidates"]] if sug else []
            want = [c for c in cands if full.get(c) == g.member]
            if sug and want:
                st.suggested += 1
                st.top += cands[0] == want[0]
                services.confirm_name(ws, meeting_id, sug["heard"], want[0])
                usable.add(i)
        self.name_stats[m.name] = st
        return usable

    def run(self, m: Meeting, instructions: str) -> Run:
        ws = get_workspace(m.gold.workspace)
        self._use_list(m)
        meeting = services.create_meeting(ws, m.name, None)
        saved = prompts.EXTRACT_INSTRUCTIONS
        prompts.EXTRACT_INSTRUCTIONS = instructions  # pipeline.extract_segment reads it at call time
        errors = []
        try:
            if m.agenda is not None:
                services.set_agenda(ws, meeting.id, text=m.agenda, source="text")
                services.approve_agenda(ws, meeting.id)
                services.set_transcript(ws, meeting.id, m.transcript, source="paste")
            else:  # CLAUDE.md rule 2: propose headings from the transcript, approved as the user would
                services.set_transcript(ws, meeting.id, m.transcript, source="paste")
                try:  # same steps as api.propose_agenda, then the Approve button
                    tree = pipeline.propose_agenda(self.provider, config.load_settings().models, ws.id, m.transcript)
                except (ProviderError, pipeline.PipelineError) as e:
                    return Run("", [], [], [f"agenda proposal failed: {e}"])
                if not tree:
                    return Run("", [], [], ["agenda proposal returned no headings"])
                services.set_agenda(ws, meeting.id, tree, source="text")
                services.approve_agenda(ws, meeting.id)
            # the user answers name suggestions before generating; process_meeting reads the confirmations
            usable = self._confirm_names(ws, meeting.id, m) if m.members_path else set()
            try:
                services.process_meeting(ws, meeting.id, provider=self.provider)
            except services.ServiceError as e:  # every segment failed; render still shows what the app would
                errors.append(str(e))
            md = services.render_meeting(ws, meeting.id)
            with session_for(ws) as s:
                rev = services.current_agenda(s, meeting.id, approved_only=True)
                tree = from_dicts(rev.tree if rev else [])
                text = {n.id: n.text for n in s.scalars(select(Note).where(Note.meeting_id == meeting.id))}
                rows = list(s.scalars(select(ReviewFinding).where(ReviewFinding.meeting_id == meeting.id)))
            errors += [f.message for f in rows if f.category in ("segment_failed", "segment_partial", "review_failed")]
            last = max((f.round_no for f in rows), default=0)
            high = {
                (
                    f.note_id,
                    f.category,
                ): f"HIGH {f.category} finding: {f.message} (note: '{text.get(f.note_id or '', '')[:80]}')"
                for f in rows
                if f.severity == "high" and f.category in HIGH_CATEGORIES and f.round_no in (0, last)
            }
            return Run(md, tree, list(high.values()), errors, usable)
        finally:
            prompts.EXTRACT_INSTRUCTIONS = saved
            with contextlib.suppress(services.ServiceError):  # e.g. interrupted mid-processing; tmp dir goes anyway
                services.delete_meeting(ws, meeting.id)

    def reflect(self, prompt: str | list[dict[str, Any]]) -> str:
        """GEPA's reflection LM: the same local model through the app's provider."""
        if not isinstance(prompt, str):
            raise TypeError("text-only reflection prompts expected")
        req = GenerateRequest(
            model=self.model,
            system="You improve instructions for a local model that files church meeting notes under agenda items.",
            prompt=prompt,
            temperature=0.7,
            max_tokens=4096,
            purpose="review",
        )
        return self.provider.generate(req)
