"""Agenda tree: parsing from indented text, stable node ids, flattening, validation.

A tree is a list of nodes: {"id": str, "text": str, "kind": "group"|"topic", "children": [...]}.
Ids are stable across edits; new nodes get fresh ids.
"""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterator
from typing import Any

from pydantic import BaseModel, Field

_BULLET = re.compile(r"^\s*(?:[-*•◦▪‣]|\d+[.)]|[a-zA-Z][.)]|[ivxIVX]+[.)])\s+")


class AgendaNode(BaseModel):
    id: str = Field(default_factory=lambda: "n" + secrets.token_hex(4))
    text: str
    kind: str = "topic"  # group | topic
    children: list[AgendaNode] = Field(default_factory=list)


def parse_text(text: str) -> list[AgendaNode]:
    """Indentation (or bullet depth) defines nesting. Text is kept verbatim after the bullet marker."""
    roots: list[AgendaNode] = []
    stack: list[tuple[int, AgendaNode]] = []  # (indent, node)
    for raw in text.splitlines():
        if not raw.strip():
            continue
        line = raw.replace("\t", "    ")
        indent = len(line) - len(line.lstrip(" "))
        content = _BULLET.sub("", line.strip(), count=1).strip() or line.strip()
        node = AgendaNode(text=content)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1].children.append(node)
        else:
            roots.append(node)
        stack.append((indent, node))
    _mark_groups(roots)
    return roots


def _mark_groups(nodes: list[AgendaNode]) -> None:
    for n in nodes:
        if n.children:
            n.kind = "group"
            _mark_groups(n.children)


def walk(nodes: list[AgendaNode], depth: int = 0) -> Iterator[tuple[AgendaNode, int]]:
    for n in nodes:
        yield n, depth
        yield from walk(n.children, depth + 1)


def node_ids(nodes: list[AgendaNode]) -> set[str]:
    return {n.id for n, _ in walk(nodes)}


def to_dicts(nodes: list[AgendaNode]) -> list[dict[str, Any]]:
    return [n.model_dump() for n in nodes]


def from_dicts(data: list[dict[str, Any]]) -> list[AgendaNode]:
    return [AgendaNode.model_validate(d) for d in data]


def validate_tree(nodes: list[AgendaNode]) -> None:
    seen: set[str] = set()
    for n, _ in walk(nodes):
        if not n.text.strip():
            raise ValueError("agenda item text cannot be empty")
        if n.id in seen:
            raise ValueError(f"duplicate agenda node id {n.id}")
        seen.add(n.id)
        if n.kind not in ("group", "topic"):
            raise ValueError(f"bad node kind {n.kind}")


def outline(nodes: list[AgendaNode]) -> str:
    """Compact id-tagged outline given to the model so it can address nodes by id."""
    return "\n".join(f"{'  ' * d}- [{n.id}] {n.text}" for n, d in walk(nodes))


def to_text(nodes: list[AgendaNode]) -> str:
    return "\n".join(f"{'  ' * d}{n.text}" for n, d in walk(nodes))
