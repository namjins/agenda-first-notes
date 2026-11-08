"""Deterministic Markdown rendering. The model never controls hierarchy; the approved tree does."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .agenda import AgendaNode, walk

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


_TAG_LIKE = re.compile(r"\[\s*(?:follow[\s-]*up|owner\s*:[^\]]*)\s*\]", re.I)


def clean_text(text: str) -> str:
    """Flat, with tag look-alikes removed: only validated follow_up/owner fields may render as tags."""
    return _flat(_TAG_LIKE.sub(" ", text))


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
) -> str:
    """notes: dicts with agenda_node_id, text, follow_up, owner, position, deleted."""
    by_node: dict[str, list[dict[str, Any]]] = {}
    for n in notes:
        if n.get("deleted") or not n.get("agenda_node_id"):
            continue
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

    def has_notes_below(node: AgendaNode) -> bool:
        return bool(by_node.get(node.id)) or any(has_notes_below(c) for c in node.children)

    def emit(node: AgendaNode, depth: int) -> None:
        pad = "  " * depth
        lines.append(f"{pad}- {_flat(node.text)}")
        own = by_node.get(node.id, [])
        for n in own:
            lines.append(f"{pad}  - {bullet_text(n)}")
        if not own and not node.children:
            lines.append(f"{pad}  - {NOT_DISCUSSED if coverage_complete else COVERAGE_INCOMPLETE}")
        elif node.children and not has_notes_below(node):
            lines.append(f"{pad}  - {NO_DISCUSSION if coverage_complete else COVERAGE_INCOMPLETE}")
            for c in node.children:
                emit_bare(c, depth + 1)
            return
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
    placed = {n.id for n, _ in walk(tree)}
    for n in sorted(
        (n for n in notes if is_follow_up(n) and not n.get("deleted") and n.get("agenda_node_id") in placed),
        key=lambda x: x.get("position", 0),
    ):
        t = bullet_text({**n, "follow_up": False})
        key = clean_text(n["text"]).lower().rstrip(".")
        if key not in seen:
            seen.add(key)
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
