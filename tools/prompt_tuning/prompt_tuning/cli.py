"""`tune` and `score` commands. GEPA (the gepa library dspy ships with) drives a custom adapter whose evaluate runs
the app's own pipeline; see README.md for why this is not dspy.GEPA."""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

from gepa import EvaluationBatch, GEPAAdapter, GEPAResult, optimize

from .app import BASELINE, SEED, App, NameStats, check_template, privacy, template
from .metric import (
    SYNTHETIC,
    Meeting,
    gold_warnings,
    load_meetings,
    measures,
    measures_line,
    name_forms,
    norm,
    score,
)

COMPONENT = "extract_instructions"
BUDGETS = {"light": 60, "medium": 200}  # metric calls = meeting runs (~0.5-3 min each on a 27-35B model)

REFLECTION_TEMPLATE = """A local model reads one excerpt of an auto-transcribed church meeting (no speaker labels, garbled words) and the approved agenda, and returns JSON notes filed under agenda item ids. The app then validates quotes, runs deterministic checks and renders the notes under the agenda. The fixed system prompt already holds the policy rules. You may change only these extraction instructions:
```
<curr_param>
```

Results on some meetings (agenda, notes as rendered, score 0-1, and concrete problems):
<side_info>

Write improved extraction instructions that fix the patterns behind these problems: file each note under the agenda item actually discussed (not the nearest, not the previous one), state outcomes rather than retelling the conversation, list every item that was discussed (including brief mentions near the end) so it is not shown as "Not discussed.", and keep follow-up flags only for genuine open actions.
Requirements:
- Keep the output contract exactly: a "findings" list (agenda_node_id, text, category, uncertainty, follow_up, owner, calling_event, evidence) and a "discussed" list (agenda_node_id, evidence); evidence = exact verbatim quotes from the excerpt.
- Never weaken the rules: the transcript is the only source of facts; no guessed names, owners or speakers; a question is not a decision, a suggestion is not an assignment; calling states never advance on their own; when uncertain, omit or state the uncertainty.
- General rules only. Do NOT include examples, quotes, names, dates, agenda headings or anything else copied from these meetings.
- At most 450 words, plain text bullet lines (not JSON).
Return only the new instructions inside ``` blocks."""


def _shingles(text: str, n: int = 8) -> set[tuple[str, ...]]:
    w = re.findall(r"\w+", norm(text))
    return {tuple(w[i : i + n]) for i in range(len(w) - n + 1)}


def _prompt_words() -> set[str]:
    from agenda_first_notes import prompts

    return set(re.findall(r"\w+", " ".join([BASELINE, prompts.CORE_POLICY, *prompts.PROFILES.values()]).lower()))


def member_names(meetings: list[Meeting]) -> tuple[re.Pattern[str] | None, set[str]]:
    """(pattern for any "first last" member name, or surname of 4+ letters not already in prompts.py; every word of
    every member name), over all loaded member lists. Built at run time, never printed."""
    known, found, words = _prompt_words(), set(), set()
    for rows in {id(m.members): m.members for m in meetings}.values():
        for row in rows:
            for form in name_forms(row):
                w = norm(form).split()
                words |= set(w)
                if len(w) > 1:
                    found.add(f"{w[0]} {w[-1]}")
                if len(w[-1]) >= 4 and w[-1] not in known:
                    found.add(w[-1])
    alts = "|".join(map(re.escape, sorted(found, key=len, reverse=True)))
    return (re.compile(rf"\b(?:{alts})\b") if found else None), words


Trajectory = dict[str, str]
Output = dict[str, Any]


class ExtractAdapter(GEPAAdapter[Meeting, Trajectory, Output]):
    """Candidate = {"extract_instructions": tail text}; one evaluation = one full app run. propose_new_texts stays
    None: gepa's reflective proposer runs with REFLECTION_TEMPLATE and the app's provider as reflection LM."""

    def __init__(self, app: App, meetings: list[Meeting]) -> None:
        self.app = app
        self.seen = set().union(*(_shingles(m.transcript) for m in meetings))
        self.names = member_names(meetings)[0]
        self.calls = 0

    def evaluate(
        self, batch: list[Meeting], candidate: dict[str, str], capture_traces: bool = False
    ) -> EvaluationBatch[Trajectory, Output]:
        tail = candidate[COMPONENT]
        copied = _shingles(tail) & self.seen
        named = self.names is not None and self.names.search(norm(tail)) is not None
        outs: list[Output] = []
        scores: list[float] = []
        trajs: list[Trajectory] = []
        for m in batch:
            if copied:  # rule: instructions only, no few-shot material (and no meeting content to paste into git)
                s, fb, md, agenda = 0.0, ["Instructions copy meeting text; write general rules only."], "", ""
            elif named:  # never says which: real names stay out of feedback and the reflection prompt
                s, fb, md, agenda = 0.0, ["Instructions contain a member's name; write general rules only."], "", ""
            else:
                s, fb, md, agenda, _ = _run_one(self.app, m, template(tail))
            self.calls += 1
            outs.append({"meeting": m.name, "score": s})
            scores.append(s)
            trajs.append(
                {"Agenda": agenda[:1500], "Rendered notes": md[:2500], "Score": f"{s:.2f}", "Problems": "\n".join(fb)}
            )
        return EvaluationBatch(outputs=outs, scores=scores, trajectories=trajs if capture_traces else None)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[Trajectory, Output],
        components_to_update: list[str],
    ) -> dict[str, list[Trajectory]]:
        recs = sorted(eval_batch.trajectories or [], key=lambda t: t["Score"])
        return {c: recs for c in components_to_update}


def _run_one(app: App, m: Meeting, instructions: str) -> tuple[float, list[str], str, str, Counter[str]]:
    from agenda_first_notes.agenda import to_text

    t0 = time.monotonic()
    r = app.run(m, instructions)
    s, fb = score(r.markdown, r.tree, m.gold, r.high, r.errors, r.usable)
    print(f"  {m.name}: {s:.2f} ({time.monotonic() - t0:.0f} s)", file=sys.stderr, flush=True)
    return s, fb, r.markdown, to_text(r.tree), measures(r.markdown, r.tree, m.gold)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--data",
        type=Path,
        nargs="+",
        required=True,
        help="one or more trees of meeting dirs with transcript.txt + gold.json (+ agenda.txt, else headings are "
        "proposed; + members.csv in the dir or an ancestor up to the root, for the name flow)",
    )
    p.add_argument("--model", required=True, help="local Ollama model, e.g. qwen3.5:27b")
    p.add_argument("--host", default="http://127.0.0.1:11434", help="loopback Ollama endpoint")
    p.add_argument(
        "--review-rounds", type=int, default=0, help="app review rounds per run (default 0: extraction only)"
    )
    p.add_argument(
        "--max-output-tokens", type=int, default=12288, help="match the app's Settings value (default 12288)"
    )
    p.add_argument("--think", action="store_true", help="let the model think before answering (app setting)")
    p.add_argument(
        "--review-by-segment", action="store_true", help="review long transcripts part by part (app setting)"
    )
    p.add_argument("--segment-chars", type=int, default=24000, help="transcript chunk size per request (app setting)")
    p.add_argument("--context-length", type=int, default=32768, help="model context window (app setting)")
    p.add_argument(
        "--filing-step",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="ask separately where each note belongs (app setting, on by default)",
    )


def _synthetic(data: list[Path]) -> bool:
    return all(d.resolve().is_relative_to(SYNTHETIC) for d in data)


def _guard(data: list[Path], *outputs: Path | None) -> None:
    """Output derived from real meetings (scores, feedback, run state, instructions) never lands in the repo."""
    from agenda_first_notes.config import REPO_ROOT

    if _synthetic(data):
        return
    for o in outputs:
        if o is not None and o.resolve().is_relative_to(REPO_ROOT):
            raise SystemExit(f"Refusing to write {o} inside the repository for non-synthetic data; write it elsewhere.")


def _setup(args: argparse.Namespace) -> tuple[App, list[Meeting]]:
    privacy()
    meetings = load_meetings(*args.data)
    from agenda_first_notes.agenda import parse_text

    for w in (w for m in meetings for w in gold_warnings(m, parse_text(m.agenda or ""))):
        print(f"WARNING: {w}", file=sys.stderr)
    return App(
        args.model,
        args.host,
        args.review_rounds,
        args.max_output_tokens,
        args.think,
        args.review_by_segment,
        args.segment_chars,
        args.context_length,
        args.filing_step,
    ), meetings


def _table(names: list[str], cols: dict[str, list[float]]) -> str:
    lines = [f"{'meeting':<28}" + "".join(f"{c:>12}" for c in cols)]
    lines += [f"{n:<28}" + "".join(f"{v[i]:>12.2f}" for v in cols.values()) for i, n in enumerate(names)]
    lines.append(f"{'mean':<28}" + "".join(f"{sum(v) / len(v):>12.2f}" for v in cols.values()))
    return "\n".join(lines)


def score_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="score", description="Score extraction instructions on a meeting set.")
    _common(p)
    p.add_argument(
        "--instructions", type=Path, help="full EXTRACT_INSTRUCTIONS text (as written by tune); default: prompts.py"
    )
    p.add_argument("--save-notes", type=Path, help="directory for each meeting's rendered notes (outside the repo)")
    args = p.parse_args(argv)
    _guard(args.data, args.save_notes)
    instructions = check_template(args.instructions.read_text("utf-8")) if args.instructions else BASELINE
    app, meetings = _setup(args)
    t0 = time.monotonic()
    try:
        results = [(m.name, _run_one(app, m, instructions)) for m in meetings]
    finally:
        app.close()
    if args.save_notes:
        for n, (_, _, md, _, _) in results:
            f = args.save_notes / f"{n.replace('/', '_')}.md"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(md, "utf-8")
    print(_table([n for n, _ in results], {"score": [r[0] for _, r in results]}))
    for n, (_, fb, _, _, _) in results:
        print(f"\n{n}:\n  " + "\n  ".join(fb))
    print("\n" + measures_line(sum((r[4] for _, r in results), Counter())))
    if line := _names_line(app.name_stats):
        print(f"\n{line}")
    print(f"\n{len(results)} meetings in {time.monotonic() - t0:.0f} s")


def _names_line(stats: dict[str, NameStats]) -> str | None:
    """Counts only, never which names."""
    n, ex = sum(s.total for s in stats.values()), sum(s.exact for s in stats.values())
    if not n:
        return None
    top, sug = sum(s.top for s in stats.values()), sum(s.suggested for s in stats.values())
    return (
        f"Name suggestions (reported, not tuned): the gold member was the top candidate for {top}/{n - ex} gold "
        f"names and suggested at all for {sug}/{n - ex}; {ex} more were said in full (nothing to confirm)."
    )


def _leaked_words(text: str, meetings: list[Meeting]) -> list[str]:
    """Capitalised words in the tuned text that occur in the meetings but nowhere in prompts.py: possible names."""
    known = _prompt_words()
    seen = set(re.findall(r"\w+", " ".join(f"{m.agenda or ''} {m.transcript}" for m in meetings).lower()))
    return sorted({w for w in re.findall(r"\b[A-Z][\w'-]+", text) if w.lower() not in known and w.lower() in seen})


def tune_main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="tune", description="GEPA-tune the extraction instructions against local Ollama.")
    _common(p)
    p.add_argument("--budget", choices=sorted(BUDGETS), default="light")
    p.add_argument("--max-metric-calls", type=int, help="override the budget (meeting runs)")
    p.add_argument("--out", type=Path, required=True, help="file for the best full EXTRACT_INSTRUCTIONS text")
    p.add_argument("--run-dir", type=Path, help="GEPA state for resuming; holds meeting-derived scores")
    args = p.parse_args(argv)
    report_path = args.out.with_name(args.out.name + ".report.txt")
    _guard(args.data, args.out, report_path, args.run_dir)
    app, meetings = _setup(args)
    # shortcut: fixed split by relative-path order, no stratification by workspace or agenda/no-agenda; use a gold
    # "split" key if real sets need it.
    val = meetings[2::3]  # every third meeting is held out for selection
    train = [m for m in meetings if m not in val]
    adapter = ExtractAdapter(app, meetings)
    t0 = time.monotonic()
    try:
        result: GEPAResult[Output, int] = optimize(
            seed_candidate={COMPONENT: SEED},
            trainset=train,
            valset=val,
            adapter=adapter,
            reflection_lm=app.reflect,
            reflection_prompt_template=REFLECTION_TEMPLATE,
            reflection_minibatch_size=3,
            max_metric_calls=args.max_metric_calls or BUDGETS[args.budget],
            candidate_selection_strategy="pareto",
            cache_evaluation=True,
            track_best_outputs=False,
            run_dir=str(args.run_dir) if args.run_dir else None,
            use_wandb=False,
            use_mlflow=False,
            seed=0,
        )
    finally:
        app.close()
    best = result.best_idx
    tail = result.candidates[best][COMPONENT]
    args.out.write_text(template(tail), "utf-8")
    names = [m.name for m in val]
    report = [
        f"GEPA: {len(result.candidates)} candidates, {adapter.calls} meeting runs, {time.monotonic() - t0:.0f} s.",
        f"Best candidate #{best}; validation scores (baseline = current prompts.py):",
        _table(names, {"baseline": [result.val_subscores[0][i] for i in range(len(val))],
                       "best": [result.val_subscores[best][i] for i in range(len(val))]}),
    ]  # fmt: skip
    if line := _names_line(app.name_stats):
        report.append(line)
    new = _leaked_words(tail, meetings)
    if not _synthetic(args.data):  # real member names never go into the report or the terminal
        member_words = member_names(meetings)[1]
        hidden = [w for w in new if w.lower() in member_words]
        new = [w for w in new if w.lower() not in member_words]
        if hidden:
            report.append(
                f"Review before pasting - {len(hidden)} words in the tuned text are member names (not shown)."
            )
    if new:
        report.append(
            f"Review before pasting - capitalised words from the meetings, not in prompts.py: {', '.join(new)}"
        )
    report_path.write_text("\n".join(report) + "\n", "utf-8")
    print("\n".join(report))
    print(f"\nWrote {args.out} and {report_path}. Compare on every meeting: score --instructions {args.out}")
