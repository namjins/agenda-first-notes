"""Deterministic Markdown rendering. The model never controls hierarchy; the approved tree does."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .agenda import AgendaNode, walk
from .evidence import similar_claim

FOLLOW_UP_HEADING = "Next Meeting – Follow-Up Items"
NO_FOLLOW_UPS = "No follow-up items identified."
NOT_DISCUSSED = "Not discussed."
NO_DISCUSSION = "No discussion."
COVERAGE_INCOMPLETE = "No notes recorded; transcript processing is incomplete, so absence is not confirmed."
NO_CLEAR_OUTCOME = "Discussion occurred; no clear decisions or action items identified."


def human_date(iso: str | None) -> str | None:
    if not iso:
        return None
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _flat(text: str) -> str:
    """One line, no Markdown structure injection (a note or heading cannot start a new list/heading)."""
    return " ".join(text.split())


_DASH = r"[\s\-\u2010-\u2015\u2212]*"  # any hyphen/dash look-alike, or none
_TAG_LIKE = re.compile(
    rf"\[\s*(?:follow{_DASH}ups?\b|owner\b)[^\]]*\]|\(\s*(?:follow{_DASH}ups?|owner\b[^)]*)\s*\)", re.I
)
_BLOCK_START = re.compile(r"^(?:[#>*+\-\u2010-\u2015\u2212]+|\d+[.)])(?:\s+|$)")
_EMPHASIS = re.compile(r"\*+|~~+|`+|(?<!\w)_+|_+(?!\w)")  # underscores inside words (snake_case) stay


def clean_text(text: str) -> str:
    """Flat plain text: tag look-alikes removed (only validated follow_up/owner fields render as tags), no leading
    Markdown block marker (heading, list, quote) and no emphasis markers, so a note stays one plain bullet."""
    t = _flat(_EMPHASIS.sub("", _TAG_LIKE.sub(" ", text)))
    while (m := _BLOCK_START.match(t)) is not None:
        t = t[m.end() :]
    return t


def is_marker(note: dict[str, Any]) -> bool:
    """A stored "item was discussed" record (verified quotes, no outcome). Never rendered as a bullet itself."""
    return note.get("text") == NO_CLEAR_OUTCOME


def is_follow_up(note: dict[str, Any]) -> bool:
    """Completed items are never follow-ups (rule 10)."""
    return bool(note.get("follow_up")) and note.get("category") != "completion"


def bullet_text(note: dict[str, Any]) -> str:
    text = clean_text(note["text"])
    tags = []
    if is_follow_up(note):
        tags.append("[Follow-up]")
    owner = _flat((note.get("owner") or "").replace("[", " ").replace("]", " "))
    if owner:
        tags.append(f"[Owner: {owner}]")
    return " ".join([text, *tags]) if tags else text


def render(
    *,
    title: str,
    meeting_date: str | None,
    tree: list[AgendaNode],
    notes: list[dict[str, Any]],
    coverage_complete: bool,
    extra_follow_ups: list[str] | None = None,
    transcript_version_id: str | None = None,
) -> str:
    """notes: dicts with agenda_node_id, text, follow_up, owner, origin, position, deleted, evidence, stale, status.

    Deleted notes and discussion markers are not rendered as bullets, but a note whose evidence proves its item was
    discussed (a marker, or a note the reviewer removed) gives an item with nothing rendered on or below it the
    NO_CLEAR_OUTCOME line instead of "Not discussed." (rules 1 and 5). Evidence counts only when it is current
    (not stale; from `transcript_version_id` when given) and the note was not deleted by the user: deleting the
    marker too is the user's explicit "not discussed"."""
    by_node: dict[str, list[dict[str, Any]]] = {}
    discussed: set[str] = set()
    for n in notes:
        if not n.get("agenda_node_id"):
            continue
        current = [
            e for e in n.get("evidence") or [] if transcript_version_id in (None, e.get("transcript_version_id"))
        ]
        user_deleted = n.get("deleted") and n.get("origin") == "user"
        if current and not n.get("stale") and n.get("status") != "unsupported" and not user_deleted:
            discussed.add(n["agenda_node_id"])
        if not n.get("deleted") and not is_marker(n):
            by_node.setdefault(n["agenda_node_id"], []).append(n)
    for lst in by_node.values():
        lst.sort(key=lambda x: x.get("position", 0))

    lines: list[str] = []
    header = title.strip()
    hd = human_date(meeting_date)
    if hd:
        header = f"{header} – {hd}" if header else hd
    if header:
        lines.append(header)
        lines.append("")

    def content_below(node: AgendaNode) -> bool:
        return bool(by_node.get(node.id)) or node.id in discussed or any(content_below(c) for c in node.children)

    def emit(node: AgendaNode, depth: int) -> None:
        pad = "  " * depth
        lines.append(f"{pad}- {_flat(node.text)}")
        own = by_node.get(node.id, [])
        for n in own:
            lines.append(f"{pad}  - {bullet_text(n)}")
        if not own and not node.children:
            absent = NOT_DISCUSSED if coverage_complete else COVERAGE_INCOMPLETE
            lines.append(f"{pad}  - {NO_CLEAR_OUTCOME if node.id in discussed else absent}")
        elif node.children and not content_below(node):
            lines.append(f"{pad}  - {NO_DISCUSSION if coverage_complete else COVERAGE_INCOMPLETE}")
            for c in node.children:
                emit_bare(c, depth + 1)
            return
        elif not own and node.id in discussed and not any(content_below(c) for c in node.children):
            lines.append(f"{pad}  - {NO_CLEAR_OUTCOME}")  # never on a parent whose descendants were discussed
        for c in node.children:
            emit(c, depth + 1)

    def emit_bare(node: AgendaNode, depth: int) -> None:
        lines.append(f"{'  ' * depth}- {_flat(node.text)}")
        for c in node.children:
            emit_bare(c, depth + 1)

    for root in tree:
        emit(root, 0)

    lines.append("")
    lines.append(FOLLOW_UP_HEADING)
    items: list[str] = []
    seen: set[str] = set()
    listed: list[tuple[str, list[str]]] = []
    placed = {n.id for n, _ in walk(tree)}
    for n in sorted(
        (n for n in notes if is_follow_up(n) and not n.get("deleted") and n.get("agenda_node_id") in placed),
        key=lambda x: x.get("position", 0),
    ):
        t = bullet_text({**n, "follow_up": False})
        key = clean_text(n["text"]).lower().rstrip(".")
        # The same follow-up filed under two agenda items is listed once (rule 10). A note without evidence (typed
        # by the user) counts its own words as transcript facts, so only wording-only differences are folded.
        quotes = [e["quote"] for e in n.get("evidence") or [] if e.get("quote")] or [n["text"]]
        if key not in seen and not any(similar_claim(k, n["text"], kq, quotes) for k, kq in listed):
            seen.add(key)
            listed.append((n["text"], quotes))
            items.append(t)
    for t in extra_follow_ups or []:
        t = clean_text(t)
        key = t.lower().rstrip(".")
        if key and key not in seen:
            seen.add(key)
            items.append(t)
    if items:
        lines.extend(f"- {t}" for t in items)
    else:
        lines.append(NO_FOLLOW_UPS)
    return "\n".join(lines).rstrip() + "\n"


def to_plain_text(markdown: str) -> str:
    """Markdown here is already plain bullets; plain text just swaps '- ' for '• '."""
    out = []
    for line in markdown.splitlines():
        stripped = line.lstrip(" ")
        if stripped.startswith("- "):
            out.append(line[: len(line) - len(stripped)] + "• " + stripped[2:])
        else:
            out.append(line)
    return "\n".join(out) + "\n"
