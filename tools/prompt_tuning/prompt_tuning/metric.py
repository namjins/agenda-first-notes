"""Gold files and the score. Pure apart from reading the data directory: no model, no app state."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from agenda_first_notes import names, render
from agenda_first_notes.agenda import AgendaNode, walk

WORKSPACES = ("ward_council", "bishopric")
SYNTHETIC = (Path(__file__).resolve().parents[1] / "data" / "synthetic").resolve()
MEMBER_FILES = ("members.csv", "Members.csv")
ABSENT = {render.NOT_DISCUSSED, render.NO_DISCUSSION}
PLACEHOLDERS = {*ABSENT, render.COVERAGE_INCOMPLETE, render.NO_CLEAR_OUTCOME}
HIGH_CATEGORIES = {"name", "number", "overconfident", "wrong_placement", "unsupported"}
RETELLING = re.compile(
    r"^(a participant|participants|someone|discussion indicated|it was (noted|mentioned|said|discussed)"
    r"|there was (a )?discussion|the group discussed)\b",
    re.I,
)
# Forbidden entries target assertions ("accepted", "approved"); keywords are substrings and cannot see negation,
# so a note that is itself negated or hedged ("has not yet accepted", "no decision") does not trip one.
# shortcut: lexical negation check, also skips a genuinely wrong note that happens to contain "no"; upgrade to a
# per-entry "negatable": false flag if real gold needs it.
NEGATED = re.compile(r"\b(not|no|nobody|never|unclear|undecided|pending|unconfirmed|yet to)\b|n't\b", re.I)
# Penalty weights. score = recall / (1 + penalties): recall is the share of expected facts found under the right
# item; dividing (not subtracting) keeps bad outputs ordered instead of all clamped to 0, which GEPA needs.
# A misfiled fact already costs recall and (if its item is left empty) a false absence; no third penalty.
W_FALSE_ABSENCE, W_NOT_DISCUSSED, W_FORBIDDEN, W_FOLLOW_UP = 0.15, 0.10, 0.20, 0.05
W_HIGH, CAP_HIGH, W_RETELL, CAP_RETELL = 0.05, 0.30, 0.02, 0.10
W_HEADINGS, CAP_HEADINGS, W_DETAIL = 0.02, 0.10, 0.05  # per proposed heading over max_headings; detail-level signal
DETAIL_RANGE = (0.5, 2.0)  # rendered/reference word ratio outside this range is penalised
TAG = re.compile(r"\[(Follow-up|Owner:[^\]]*)\]")
MAX_FEEDBACK_LINES = 14  # keeps the reflection prompt small enough for a 27-35B local model

_FOLD = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
                       "“": '"', "”": '"', "„": '"', "″": '"',
                       "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
                       "−": "-"})  # fmt: skip


def norm(text: str) -> str:
    """Case, Unicode (NFC), curly quotes, dashes and whitespace folded: how gold headings and keywords compare."""
    return " ".join(unicodedata.normalize("NFC", text).translate(_FOLD).casefold().split())


Keywords = list[list[str]]


@dataclass
class Expected:
    item: str | None  # None: found if any note anywhere matches
    keywords: Keywords
    follow_up: bool | None
    also: list[str] = field(default_factory=list)  # other headings where the fact also counts as found


@dataclass
class Forbidden:
    keywords: Keywords
    why: str


@dataclass
class GoldName:
    heard: str  # as in the transcript
    member: str  # the member list's Full Name
    # filled by load_meetings from the member list:
    forms: list[str] = field(default_factory=list)  # "First Last" spellings a note may use
    label: str = ""  # in feedback: the names for synthetic data, "gold name #n" otherwise (no real names anywhere)
    exact: bool = False  # heard text already is the member's name: the app suggests nothing, needs no confirmation


@dataclass
class Gold:
    workspace: str
    expected: list[Expected]
    not_discussed: list[str]
    forbidden: list[Forbidden]
    topics: list[Keywords] = field(default_factory=list)  # no-agenda meetings: each in some heading or note
    max_headings: int | None = None
    reference_notes: str | None = None  # file name; only its word count is used (detail level), never its content
    reference_words: int | None = None  # filled by load_meetings
    names: list[GoldName] = field(default_factory=list)


@dataclass
class Meeting:
    name: str  # path relative to --data (prefixed by the root's name when several are given)
    agenda: str | None  # None: no agenda, the app proposes headings from the transcript
    transcript: str
    gold: Gold
    members_path: Path | None = None  # nearest member list; None: the names flow is skipped
    members: list[dict[str, Any]] = field(default_factory=list)  # its rows (shared by meetings using that list)


def _keywords(v: Any, where: str) -> Keywords:
    if not (isinstance(v, list) and v and all(isinstance(g, list) and g for g in v)):
        raise ValueError(f"{where}: keywords must be a non-empty list of non-empty lists")
    if not all(isinstance(a, str) and a.strip() for g in v for a in g):
        raise ValueError(f"{where}: every keyword alternative must be a non-empty string")
    return [[a for a in g] for g in v]


def parse_gold(data: Any, where: str) -> Gold:
    if not isinstance(data, dict) or data.get("workspace") not in WORKSPACES:
        raise ValueError(f"{where}: 'workspace' must be one of {WORKSPACES}")
    expected = []
    for i, e in enumerate(data.get("expected", [])):
        fu = e.get("follow_up")
        also = e.get("also", [])
        if not isinstance(e.get("item"), str | None) or fu not in (True, False, None):
            raise ValueError(f"{where}: expected[{i}] needs a string or null 'item' and follow_up true/false/null")
        if not isinstance(also, list) or not all(isinstance(h, str) for h in also) or (also and e["item"] is None):
            raise ValueError(f"{where}: expected[{i}] 'also' must be a list of headings, next to a non-null 'item'")
        expected.append(Expected(e["item"], _keywords(e.get("keywords"), f"{where} expected[{i}]"), fu, also))
    nd = data.get("not_discussed", [])
    if not all(isinstance(h, str) for h in nd):
        raise ValueError(f"{where}: not_discussed must be a list of headings")
    forbidden = [
        Forbidden(_keywords(f.get("keywords"), f"{where} forbidden[{i}]"), str(f.get("why", "")))
        for i, f in enumerate(data.get("forbidden", []))
    ]
    # A topic is a list of alternatives (["budget", "finance"]) or full keywords ([["budget"], ["deadline"]]).
    topics = [
        _keywords(t if t and all(isinstance(g, list) for g in t) else [t], f"{where} topics[{i}]")
        for i, t in enumerate(data.get("topics", []))
    ]
    mh, ref = data.get("max_headings"), data.get("reference_notes")
    if not (mh is None or (isinstance(mh, int) and mh > 0)) or not isinstance(ref, str | None):
        raise ValueError(f"{where}: max_headings must be a positive integer, reference_notes a file name")
    gnames = []
    for i, n in enumerate(data.get("names", [])):
        if not (isinstance(n, dict) and all(isinstance(n.get(k), str) and n[k].strip() for k in ("heard", "member"))):
            raise ValueError(f"{where}: names[{i}] needs non-empty 'heard' and 'member' (the list's Full Name)")
        gnames.append(GoldName(n["heard"], n["member"]))
    return Gold(data["workspace"], expected, list(nd), forbidden, topics, mh, ref, names=gnames)


def name_forms(row: dict[str, Any]) -> list[str]:
    """How a note may write a member: full and preferred name in display order, and each as "First Last"."""
    full = [names.display(row["full_name"]), names.display(row.get("preferred_name") or row["full_name"])]
    return list(dict.fromkeys([*full, *(f"{w[0]} {w[-1]}" for w in (x.split() for x in full) if len(w) > 1)]))


def member_list(d: Path, root: Path) -> Path | None:
    """The nearest member list in d or an ancestor, up to and including root."""
    for p in (d, *d.parents):
        for f in MEMBER_FILES:
            if (p / f).is_file():
                return p / f
        if p == root:
            break
    return None


def _bind_names(gold: Gold, rows: list[dict[str, Any]], show: bool, where: str) -> None:
    """Fill each gold name from its member row. `show`: synthetic data, so feedback may print the names."""
    for i, g in enumerate(gold.names):
        row = next((r for r in rows if r["full_name"] == g.member), None)
        if row is None:  # no name in the message: real gold must not print one
            raise ValueError(f"{where}: names[{i}] member is not a Full Name in the member list")
        g.forms = name_forms(row)
        full = (row["full_name"], row.get("preferred_name") or row["full_name"])
        g.exact = norm(g.heard) in {norm(names.display(x)) for x in full}  # what names.suggest skips as exact
        g.label = f"'{g.forms[-1]}' for '{g.heard}'" if show else f"gold name #{i + 1}"


def _strip_marker(text: str) -> str:
    """Synthetic files start with a 'SYNTHETIC ...' line (repo audit); it is not part of the meeting."""
    first, _, rest = text.partition("\n")
    return rest if first.startswith("SYNTHETIC") else text


def words(text: str) -> int:
    return len(re.findall(r"\w+", text))


def reference_words(text: str) -> int:
    """Words in a reference notes file, without headings, placeholders or the final follow-up list.
    shortcut: a heading is a '#' line, a line ending in ':' or a line followed by a deeper-indented one; fine for
    bullet outlines like the app's, overcounts flat prose layouts slightly."""
    lines = [ln.rstrip() for ln in _strip_marker(text).splitlines() if ln.strip()]
    indent = [len(ln) - len(ln.lstrip()) for ln in lines]
    n = 0
    for i, ln in enumerate(lines):
        body = ln.strip().lstrip("-*•# ").strip()
        if norm(body).startswith("next meeting"):
            break
        heading = (
            ln.lstrip().startswith("#") or body.endswith(":") or (i + 1 < len(lines) and indent[i + 1] > indent[i])
        )
        if not heading and body not in PLACEHOLDERS:
            n += words(TAG.sub("", body))
    return n


def load_meetings(*roots: Path) -> list[Meeting]:
    """Every directory under the roots (any depth) holding transcript.txt and gold.json, sorted by name: the path
    relative to its root, prefixed by the root's directory name when there are several roots. agenda.txt is
    optional: without it the meeting is scored through the app's agenda-proposal path. Each meeting uses the nearest
    members.csv in its directory or an ancestor up to its root, parsed once per file."""
    out: list[Meeting] = []
    lists: dict[Path, list[dict[str, Any]]] = {}
    for root in roots:
        show = root.resolve().is_relative_to(SYNTHETIC)
        for t in root.rglob("transcript.txt"):
            d, rel = t.parent, t.parent.relative_to(root).as_posix()
            name = PurePosixPath(root.resolve().name, rel).as_posix() if len(roots) > 1 else rel
            if not (d / "gold.json").is_file():
                print(f"WARNING: skipping {name}: no gold.json", file=sys.stderr)
                continue
            agenda = _strip_marker((d / "agenda.txt").read_text("utf-8")) if (d / "agenda.txt").is_file() else None
            gold = parse_gold(json.loads((d / "gold.json").read_text("utf-8")), f"{name}/gold.json")
            if gold.reference_notes:
                ref = d / gold.reference_notes
                if ref.parent != d or not ref.is_file():
                    raise ValueError(f"{name}/gold.json: reference_notes {gold.reference_notes!r} is not a file here")
                gold.reference_words = reference_words(ref.read_text("utf-8"))
            mp = member_list(d, root)
            if mp is not None and mp not in lists:  # read at run time only; never printed
                lists[mp] = names.parse_members_csv(mp.read_bytes())
            rows = lists.get(mp, []) if mp is not None else []
            if gold.names and mp is None:
                print(f"WARNING: {name}: gold names ignored, no member list found", file=sys.stderr)
                gold.names = []
            _bind_names(gold, rows, show, f"{name}/gold.json")
            out.append(Meeting(name, agenda, _strip_marker(t.read_text("utf-8")), gold, mp, rows))
    out.sort(key=lambda m: m.name)
    if dup := sorted(n for n, c in Counter(m.name for m in out).items() if c > 1):
        raise SystemExit(f"Meeting names repeat across --data roots (same directory name?): {', '.join(dup)}")
    if not out:
        raise SystemExit(f"No meetings found in {', '.join(map(str, roots))}")
    return out


def _paths(tree: list[AgendaNode]) -> list[list[AgendaNode]]:
    out: list[list[AgendaNode]] = []
    stack: list[AgendaNode] = []
    for node, depth in walk(tree):
        stack[depth:] = [node]
        out.append(list(stack))
    return out


def resolve(tree: list[AgendaNode], heading: str) -> tuple[AgendaNode | None, str | None]:
    """Gold heading -> agenda node, by folded text; 'Parent > Child' disambiguates repeated headings."""
    wants = [[norm(heading)]] + ([[norm(p) for p in heading.split(" > ")]] if " > " in heading else [])
    for want in wants:
        hits = [p[-1] for p in _paths(tree) if [norm(n.text) for n in p][-len(want) :] == want]
        if len(hits) == 1:
            return hits[0], None
        if len(hits) > 1:
            return None, f"gold heading {heading!r} matches {len(hits)} agenda items; write it as 'Parent > {heading}'"
    return None, f"gold heading {heading!r} matches no agenda item"


def gold_warnings(m: Meeting, tree: list[AgendaNode]) -> list[str]:
    heads = [h for e in m.gold.expected for h in [e.item, *e.also] if h is not None] + m.gold.not_discussed
    if m.agenda is None:  # headings are proposed at run time; a fixed heading only counts if one matches it exactly
        return [f"{m.name}: no agenda, so heading {h!r} is scored only if a proposed heading matches it; use null"
                for h in heads]  # fmt: skip
    return [f"{m.name}: {w} (skipped)" for h in heads if (w := resolve(tree, h)[1])]


def sections(md: str, tree: list[AgendaNode]) -> dict[str, list[str]]:
    """Node id -> bullet texts rendered directly under it (notes and placeholders), read back from the Markdown.
    render.render emits headings in tree order, each followed by its own bullets, so a line that is the next
    expected heading line advances; every other bullet belongs to the current heading.
    shortcut: a note whose text equals the next heading at the same indent is read as that heading; vanishingly rare."""
    order = list(walk(tree))
    out: dict[str, list[str]] = {n.id: [] for n, _ in order}
    i, cur = 0, None
    for line in md.splitlines():
        if line == render.FOLLOW_UP_HEADING:
            break
        if i < len(order) and line == f"{'  ' * order[i][1]}- {' '.join(order[i][0].text.split())}":
            cur, i = order[i][0], i + 1
        elif cur is not None and line.lstrip().startswith("- "):
            out[cur.id].append(line.lstrip()[2:])
    return out


def matches(text: str, keywords: Keywords) -> bool:
    """Every group has an alternative that starts a word in the text: gold keywords are stems ("reimburs"), and
    matching inside words made coincidences ("uti" in "duties", "3rd" in "23rd")."""
    t = norm(text)
    return all(any(re.search(r"(?<!\w)" + re.escape(norm(alt)), t) for alt in group) for group in keywords)


def measures(md: str, tree: list[AgendaNode], gold: Gold) -> Counter[str]:
    """Counts behind the score, reported separately: facts captured anywhere, facts on the right item, agenda items
    with facts and how many of those read as absent, and wrong content (forbidden facts, notes on undiscussed items)."""
    sec = sections(md, tree)
    notes = {nid: [b for b in bs if b not in PLACEHOLDERS] for nid, bs in sec.items()}
    every = [b for bs in notes.values() for b in bs]
    parents = {p[-1].id: [x.id for x in p[:-1]] for p in _paths(tree)}
    c: Counter[str] = Counter()
    items: set[str] = set()
    for e in gold.expected:
        node = resolve(tree, e.item)[0] if e.item is not None else None
        if e.item is not None and node is None:
            continue  # unresolvable gold heading (warned at load)
        c["facts"] += 1
        c["captured"] += any(matches(b, e.keywords) for b in every)
        ok = [node.id, *(a.id for h in e.also if (a := resolve(tree, h)[0]))] if node else list(notes)
        c["on_item"] += any(matches(b, e.keywords) for nid in ok for b in notes[nid])
        if node is not None:
            items.add(node.id)
    c["items"] = len(items)
    c["false_absent"] = sum(
        bool(ABSENT & set(sec[i]) or any(render.NO_DISCUSSION in sec[p] for p in parents[i])) for i in items
    )
    c["wrong"] = sum(1 for f in gold.forbidden for b in every if matches(b, f.keywords) and not NEGATED.search(b))
    for h in gold.not_discussed:
        if (n := resolve(tree, h)[0]) is not None:
            c["wrong"] += bool([b for b in sec[n.id] if b not in ABSENT | {render.COVERAGE_INCOMPLETE}])
    c["headings"] = sum(1 for _ in walk(tree))
    return c


def measures_line(c: Counter[str]) -> str:
    def pct(a: str, b: str) -> str:
        return f"{c[a]}/{c[b]} ({c[a] / c[b]:.0%})" if c[b] else "n/a"

    return (f"Facts captured anywhere {pct('captured', 'facts')}, on the right item {pct('on_item', 'facts')}; "
            f"discussed items shown as not discussed {pct('false_absent', 'items')}; wrong-content notes {c['wrong']}.")  # fmt: skip


def _label(keywords: Keywords) -> str:
    return " + ".join(repr(g[0]) for g in keywords)


def score(
    md: str,
    tree: list[AgendaNode],
    gold: Gold,
    high: list[str],
    errors: list[str],
    usable: set[int] | None = None,
) -> tuple[float, list[str]]:
    """(score in [0, 1], concrete feedback lines). `high`: HIGH deterministic findings already formatted;
    `errors`: processing failures. Unresolvable gold headings are skipped here (warned at load). `usable`: indexes of
    gold names the notes may use (confirmed, or said in full); None = all. A name the simulated user could not
    confirm (the app did not suggest it) is no fault of the instructions, so it is left out of the score."""
    sec = sections(md, tree)
    notes = {nid: [b for b in bs if b not in PLACEHOLDERS] for nid, bs in sec.items()}
    counts = Counter(norm(n.text) for n, _ in walk(tree))
    heading = {  # repeated headings ("Updates") are named with their parent so the feedback is unambiguous
        p[-1].id: " > ".join(x.text for x in p[-2:]) if counts[norm(p[-1].text)] > 1 else p[-1].text
        for p in _paths(tree)
    }
    parents = {p[-1].id: [x.id for x in p[:-1]] for p in _paths(tree)}
    fb = [f"Processing error: {e[:200]}" for e in errors]
    resolved = [(e, None if e.item is None else resolve(tree, e.item)[0]) for e in gold.expected]
    resolved = [(e, n) for e, n in resolved if n is not None or e.item is None]
    named = [g for i, g in enumerate(gold.names) if usable is None or i in usable]
    if (resolved or gold.topics or named) and not any(notes.values()):
        return 0.0, fb + ["No notes were rendered at all; every discussed outcome must be recorded."]

    pen, hits = 0.0, 0
    for nid in dict.fromkeys(
        n.id for _, n in resolved if n is not None
    ):  # false absence: once per item with expected facts
        if ABSENT & set(sec[nid]) or any(render.NO_DISCUSSION in sec[p] for p in parents[nid]):
            pen += W_FALSE_ABSENCE
            fb.append(f"False 'Not discussed.' for '{heading[nid]}': the transcript discusses it.")
    for e, node in resolved:
        ok = [node.id, *(a.id for h in e.also if (a := resolve(tree, h)[0]))] if node else list(notes)
        pool = [b for nid in ok for b in notes[nid]]
        where = f"under '{heading[node.id]}'" if node else "anywhere in the notes"
        here = [b for b in pool if matches(b, e.keywords)]
        if here:
            hits += 1
            if e.follow_up is not None and not any(("[Follow-up]" in b) == e.follow_up for b in here):
                pen += W_FOLLOW_UP
                want = "needs a [Follow-up] tag" if e.follow_up else "should not be tagged [Follow-up]"
                fb.append(f"Note about {_label(e.keywords)} {where} {want}.")
            continue
        if node is None:
            fb.append(f"Missing: no note about {_label(e.keywords)} anywhere in the notes.")
            continue
        elsewhere = [nid for nid, bs in notes.items() if nid not in ok and any(matches(b, e.keywords) for b in bs)]
        if elsewhere:
            fb.append(
                f"Expected {_label(e.keywords)} under '{heading[node.id]}' but it appeared under '{heading[elsewhere[0]]}'."
            )
        else:
            fb.append(f"Missing: no note about {_label(e.keywords)} under '{heading[node.id]}'.")
    for h in gold.not_discussed:
        node = resolve(tree, h)[0]
        if node is None:
            continue
        claimed = [b for b in sec[node.id] if b not in ABSENT | {render.COVERAGE_INCOMPLETE}]
        if claimed:
            pen += W_NOT_DISCUSSED
            fb.append(f"'{heading[node.id]}' was not discussed, but got: '{claimed[0][:100]}'.")
    for f in gold.forbidden:
        for nid, bs in notes.items():
            for b in bs:
                if matches(b, f.keywords) and not NEGATED.search(b):
                    pen += W_FORBIDDEN
                    fb.append(f"Wrong note under '{heading[nid]}': '{b[:100]}' ({f.why}).")
    pen += min(W_HIGH * len(high), CAP_HIGH)
    fb.extend(high[:4])
    retold = [b for bs in notes.values() for b in bs if RETELLING.match(b)]
    pen += min(W_RETELL * len(retold), CAP_RETELL)
    fb.extend(f"Retells the conversation instead of stating the outcome: '{b[:80]}'." for b in retold[:2])

    found = 0
    for t in gold.topics:
        if any(matches(n.text, t) for n, _ in walk(tree)) or any(matches(b, t) for bs in notes.values() for b in bs):
            found += 1
        else:
            fb.append(f"Proposed headings missed topic {_label(t)}: no heading or note covers it.")
    n_heads = sum(1 for _ in walk(tree))
    if gold.max_headings is not None and n_heads > gold.max_headings:
        pen += min(W_HEADINGS * (n_heads - gold.max_headings), CAP_HEADINGS)
        fb.append(f"Too many proposed headings ({n_heads}, at most {gold.max_headings}); set by the proposal step.")
    if gold.reference_words:
        mine = words(TAG.sub("", " ".join(b for bs in notes.values() for b in bs)))
        ratio, ref = mine / gold.reference_words, gold.reference_words
        if ratio < DETAIL_RANGE[0]:
            pen += W_DETAIL
            fb.append(
                f"Notes are much shorter than the reference ({mine} vs {ref} words); record more of the outcomes discussed."
            )
        elif ratio > DETAIL_RANGE[1]:
            pen += W_DETAIL
            fb.append(
                f"Notes are much longer than the reference ({mine} vs {ref} words); state outcomes briefly, no narration or repetition."
            )

    used = 0
    for g in named:
        if any(matches(b, [g.forms]) for bs in notes.values() for b in bs):
            used += 1
        else:
            fb.append(f"{'Name said in full' if g.exact else 'Confirmed name'} {g.label} never used in the notes.")

    total = len(resolved) + len(gold.topics) + len(named)
    recall = (hits + found + used) / total if total else 1.0
    s = recall / (1 + pen)
    topics = f", {found}/{len(gold.topics)} topics covered" if gold.topics else ""
    topics += f", {used}/{len(named)} member names used" if named else ""
    head = f"Score {s:.2f}: {hits}/{len(resolved)} expected facts found{topics}, penalties {pen:.2f}."
    more = [f"(+{len(fb) - MAX_FEEDBACK_LINES} more problems)"] if len(fb) > MAX_FEEDBACK_LINES else []
    return s, [head, *fb[:MAX_FEEDBACK_LINES], *more]
