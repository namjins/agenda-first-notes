"""Member-list name suggestions: which member a garbled transcript mention ("bob boss") may refer to. Pure; no I/O.

Suggestions only: nothing here resolves a name. The user confirms or rejects each one, and only a confirmed name may
reach the notes (rules 3, 7). The member list, callings and calling dates rank and explain candidates; they are never
evidence of what happened (rules 3, 8). Agenda headings are used as context for ranking, never as evidence.
"""

from __future__ import annotations

import bisect
import csv
import io
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from difflib import SequenceMatcher
from typing import Any

from . import evidence, pipeline
from .agenda import AgendaNode, walk

HEADER = ["Preferred Name", "Full Name", "Spouse Name", "Callings with Date Sustained"]
MAX_MEMBERS = 5000  # shortcut: one ward is a few hundred members; raise if a larger list must load
_DATE = re.compile(r"\(\s*(\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|\d{4}-\d{2}-\d{2})\s*\)")  # 5 Jan 2025 or 2025-01-05
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # NUL and other control characters (tab/newline kept)
_SEPS = " \t\r\n,;"


def parse_callings(cell: str) -> list[dict[str, str]]:
    """Every "<calling> (<d Mon yyyy> or <yyyy-mm-dd>)" segment, whatever separates them (comma, semicolon, newline). The calling text
    is everything between the previous date and this one, so a comma inside a calling name survives."""
    out: list[dict[str, str]] = []
    pos = 0
    for m in _DATE.finditer(cell):
        calling = " ".join(cell[pos : m.start()].strip(_SEPS).split())
        if calling:
            out.append({"calling": calling, "sustained": " ".join(m.group(1).split())})
        pos = m.end()
    rest = " ".join(cell[pos:].strip(_SEPS).split())
    if rest:  # a calling listed without a date
        out.append({"calling": rest, "sustained": ""})
    return out


def parse_members_csv(data: bytes) -> list[dict[str, Any]]:
    """Rows of the member CSV (UTF-8, BOM optional). Raises ValueError with a plain message on a wrong file."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise ValueError("The member list must be a UTF-8 CSV file.") from e
    try:
        return _rows(csv.reader(io.StringIO(_CONTROL.sub("", text), newline="")))
    except csv.Error as e:  # e.g. a cell over the csv field size limit
        raise ValueError("The member list could not be read as a CSV file.") from e


def _rows(rows: Iterator[list[str]]) -> list[dict[str, Any]]:
    header = [h.strip() for h in next(rows, [])]
    if header[: len(HEADER)] != HEADER:
        raise ValueError(f"Not a member list: the first line must be the column headings {', '.join(HEADER)}.")
    out: list[dict[str, Any]] = []
    for row in rows:
        row = [c.strip() for c in row] + [""] * (len(HEADER) - len(row))
        if not any(row):
            continue
        preferred, full, spouse, callings = row[:4]
        if not (full or preferred):
            raise ValueError(f"Row {len(out) + 2}: the member has no name.")
        out.append(
            {
                "preferred_name": preferred[:200],
                "full_name": (full or preferred)[:200],
                "spouse_name": spouse[:200],
                "callings": parse_callings(callings),
            }
        )
        if len(out) > MAX_MEMBERS:
            raise ValueError(f"The member list has more than {MAX_MEMBERS} rows.")
    return out


_SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}


def _words(part: str) -> list[str]:
    """Name words without punctuation or generational suffixes: "Robert, Jr." -> ["Robert"]."""
    return [w for w in re.sub(r"[^\w\s'-]", " ", part).split() if w.lower() not in _SUFFIXES]


def _split(name: str) -> tuple[list[str], list[str]]:
    """(given words, surname words) of "Last, Given" or of "Given Last"."""
    last, sep, given = name.partition(",")
    if sep:
        return _words(given), _words(last)
    w = _words(name)
    return w[:-1], w[-1:]


def display(name: str) -> str:
    """ "Ross, Robert James" -> "Robert James Ross"; "Ross, Robert, Jr." -> "Robert Ross"."""
    given, last = _split(name)
    return " ".join(given + last)


def calling_text(c: dict[str, str]) -> str:
    """Display only ("Primary Teacher, sustained 5 Jan 2025"); the date never enters a prompt or a note."""
    return f"{c['calling']}, sustained {c['sustained']}" if c.get("sustained") else c["calling"]


# Organisation of a calling, and of the discussion around a mention, by keyword.
ORGS: dict[str, re.Pattern[str]] = {
    name: re.compile(rf"\b(?:{p})\b", re.I)
    for name, p in {
        "Primary": r"primary",
        "Relief Society": r"relief society",
        "Elders Quorum": r"elders?'?\s*quorum",
        "Young Women": r"young women",
        "Young Men": r"young men|aaronic priesthood|deacons?|priests? quorum|teachers quorum",
        "Sunday School": r"sunday school",
        "Bishopric": r"bishop|bishopric",
        "Clerks and secretaries": r"clerks?|secretary|secretaries",
        "Music": r"music|organist|chorister",
        "Missionary work": r"missionary|missionaries|mission leader",
        "Temple and family history": r"temple|family history",
    }.items()
}


def orgs_in(text: str, capitalised: bool = False) -> set[str]:
    """Organisations named in `text`. `capitalised`: only where written as a name ("Primary", not "our primary
    concern"), for transcript text; calling names and agenda headings are matched in any case."""
    return {name for name, p in ORGS.items() if any(not capitalised or m[0][0].isupper() for m in p.finditer(text))}


# Organisations whose callings tell a member's title (Sister/Brother); mixed or other callings tell nothing.
_FEMALE_ORGS, _MALE_ORGS = {"Relief Society", "Young Women"}, {"Elders Quorum", "Bishopric", "Young Men"}
_TITLE_GENDER = {"sister": "f", "sis": "f", "brother": "m", "bro": "m", "elder": "m", "bishop": "m"}


_NICK = {
    "robert": "bob bobby rob robbie bert",
    "william": "bill billy will willy liam",
    "james": "jim jimmy jamie",
    "john": "jack johnny jon",
    "michael": "mike mikey mick",
    "david": "dave davey",
    "thomas": "tom tommy",
    "richard": "rick ricky rich dick",
    "joseph": "joe joey",
    "christopher": "chris",
    "christine": "chris chrissy",
    "katherine": "kate katie kathy kat",
    "catherine": "kate katie cathy cat",
    "kathryn": "kate katie kathy",
    "elizabeth": "liz lizzie beth betty eliza",
    "susan": "sue susie",
    "jennifer": "jen jenny",
    "matthew": "matt",
    "daniel": "dan danny",
    "steven": "steve",
    "stephen": "steve",
    "anthony": "tony",
    "edward": "ed eddie ted",
    "samuel": "sam sammy",
    "samantha": "sam sammy",
    "alexander": "alex",
    "alexandra": "alex",
    "andrew": "andy drew",
    "nicholas": "nick",
    "benjamin": "ben benny",
    "gregory": "greg",
    "jeffrey": "jeff",
    "jonathan": "jon",
    "joshua": "josh",
    "kenneth": "ken kenny",
    "lawrence": "larry",
    "patrick": "pat",
    "patricia": "pat patty trish",
    "margaret": "peggy maggie meg",
    "deborah": "deb debbie",
    "barbara": "barb",
    "timothy": "tim",
    "zachary": "zach",
    "abigail": "abby",
    "rebecca": "becky becca",
    "charles": "charlie chuck",
    "henry": "hank harry",
    "frederick": "fred",
    "nathan": "nate",
    "nathaniel": "nate",
    "philip": "phil",
    "ronald": "ron",
    "donald": "don",
    "douglas": "doug",
    "amanda": "mandy",
    "victoria": "vicky tori",
    "theodore": "ted teddy",
}
NICKNAMES: dict[str, set[str]] = {}
for _formal, _nicks in _NICK.items():
    for _n in _nicks.split():
        NICKNAMES.setdefault(_n, set()).add(_formal)


def _canon(first: str) -> set[str]:
    return {first} | NICKNAMES.get(first, set())


def _soundex(w: str) -> str:
    codes = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3")}
    codes |= {"l": "4", "m": "5", "n": "5", "r": "6"}
    w = re.sub(r"[^a-z]", "", w.lower())
    if not w:
        return ""
    out, prev = w[0].upper(), codes.get(w[0], "")
    for ch in w[1:]:
        c = codes.get(ch, "")
        if c and c != prev:
            out += c
        if ch not in "hw":
            prev = c
    return out


def sim(a: str, b: str) -> float:
    """Sound/spelling likeness of two lowercase words: 1 equal, .85 same Soundex, .75 rhyme (same Soundex after the
    first letter: boss ~ ross), .7 one Soundex a prefix of the other, else the difflib ratio."""
    if a == b:
        return 1.0
    sa, sb = _soundex(a), _soundex(b)
    if sa == sb:
        return 0.85
    m = SequenceMatcher(None, a, b)
    r = m.ratio() if m.real_quick_ratio() >= 0.6 and m.quick_ratio() >= 0.6 else 0.0
    if len(sa) > 2 and sa[1:] == sb[1:]:  # two or more sound codes must agree: "pass" is no "Tice"
        r = max(r, 0.75)
    elif sa[:1] == sb[:1] and len(min(sa, sb, key=len)) > 1 and max(sa, sb, key=len).startswith(min(sa, sb, key=len)):
        r = max(r, 0.7)
    return r


_TITLES = {"brother", "sister", "bishop", "president", "elder", "bro", "sis", "pres"}
MIN_SCORE = 0.7
_WINDOW = 300  # characters either side of a mention searched for organisation words and spouse names
_HEADING_REACH = 1500  # a heading word this far before a mention still marks the agenda item it sits under


class _Member:
    def __init__(self, i: int, d: dict[str, Any]) -> None:
        self.id, self.d = str(d.get("id") or i), d  # the stored member id: two members may share a full name
        self.name = display(d["full_name"])
        self.preferred = display(d.get("preferred_name") or d["full_name"])
        given, last = _split(d["full_name"])
        self.surname = " ".join(last).lower()
        self.first = given[0].lower() if given else ""
        pgiven, _ = _split(d.get("preferred_name") or "")
        self.pref_first = pgiven[0].lower() if pgiven else ""
        self.callings = [calling_text(c) for c in d.get("callings") or []]
        self.orgs = {o: c for c in d.get("callings") or [] for o in orgs_in(c["calling"])}
        f, m = bool(set(self.orgs) & _FEMALE_ORGS), bool(set(self.orgs) & _MALE_ORGS)
        self.gender = "f" if f and not m else "m" if m and not f else ""  # only when the callings tell
        self.spouse = display(d.get("spouse_name") or "")
        sgiven, _ = _split(d.get("spouse_name") or "")
        self.spouse_first = sgiven[0] if sgiven else ""


def _first_score(word: str, m: _Member) -> tuple[float, str]:
    """How well a heard word fits the member's first or preferred name, with the reason."""
    best: tuple[float, str] = (0.0, "")
    for name, label in ((m.first, "first name"), (m.pref_first, "preferred name")):
        if not name:
            continue
        if word == name:
            s, why = 1.0, f"{label} {name.title()}"
        elif _canon(word) & _canon(name):
            s, why = 0.9, f'"{word}" is a common nickname for {name.title()} ({label})'
        else:
            s = sim(word, name)
            why = f'"{word}" sounds like {name.title()} ({label})'
        if s > best[0]:
            best = (s, why)
    return best


def _surname_score(word: str, m: _Member) -> tuple[float, str]:
    s = sim(word, m.surname)
    why = f"surname {m.surname.title()}" if s == 1.0 else f'"{word}" sounds like the surname {m.surname.title()}'
    return s, why


def _mentions(text: str, name_starts: set[str]) -> Iterable[tuple[int, int, str, str]]:
    """(start, end, heard, kind) for titled words, capitalised name-like runs (a lone word only when it does not
    start a sentence), and lowercase word pairs whose first word is a member's first name or a nickname of one
    (`name_starts`). A possessive "'s" is no part of the heard text."""
    lower = pipeline._lower_words(text)
    toks = []
    for m in pipeline._WORD.finditer(text):
        w = m.group()[:-2] if m.group().lower().endswith("'s") else m.group()
        toks.append((m.start(), m.start() + len(w), w))
    seen = Counter(w.lower() for _, _, w in toks)
    titled = Counter(toks[k + 1][2].lower() for k in range(len(toks) - 1) if toks[k][2].lower() in _TITLES)
    paired = Counter(toks[k + 1][2].lower() for k in range(len(toks) - 1) if toks[k][2] in name_starts)

    def joined(a: int, b: int) -> bool:
        gap = text[toks[a][1] : toks[b][0]]
        return gap.isspace() or (gap.startswith(".") and gap[1:].isspace() and toks[a][2].lower() in ("bro", "sis"))

    def nameish(w: str) -> bool:  # not note vocabulary, nor (the plural of) a word the transcript uses in lowercase
        lw, st = w.lower(), evidence._stem(w.lower())
        return (
            w[0].isupper() and lw not in pipeline.NOTE_WORDS and st not in pipeline.NOTE_WORDS and not {lw, st} & lower
        )

    def sentence_start(k: int) -> bool:
        before = text[: toks[k][0]].rstrip(" \t")
        return not before or before[-1] in ".?!\n\r"

    i = 0
    while i < len(toks):
        s, e, w = toks[i]
        if w.lower() in _TITLES and i + 1 < len(toks) and joined(i, i + 1):
            nxt = toks[i + 1][2]
            # lowercase after a title ("the bishop said"): a name only if the word occurs nowhere else
            name_like = nxt[0].isupper() or seen[nxt.lower()] == titled[nxt.lower()]
            if nxt.lower() not in pipeline.NOTE_WORDS and name_like:
                yield s, toks[i + 1][1], text[s : toks[i + 1][1]], "titled"
                i += 2
                continue
        if nameish(w):
            j = i
            while j + 1 < len(toks) and j - i < 2 and joined(j, j + 1) and nameish(toks[j + 1][2]):
                j += 1
            if j > i or not sentence_start(i):  # "Honestly, ...": a capital that only starts a sentence is no name
                yield s, toks[j][1], text[s : toks[j][1]], "caps"
            i = j + 1
            continue
        if w[0].islower() and w in name_starts and i + 1 < len(toks) and joined(i, i + 1):
            nxt = toks[i + 1][2]
            # as after a title: the second word must occur nowhere else ("will teach" when "teach" recurs is no name)
            if nxt[0].islower() and nxt not in pipeline.NOTE_WORDS and seen[nxt.lower()] == paired[nxt.lower()]:
                yield s, toks[i + 1][1], text[s : toks[i + 1][1]], "pair"
        i += 1


def _score(heard_words: list[str], kind: str, m: _Member) -> tuple[float, str]:
    """Name-only likeness of a mention to a member (0..1) and a plain-English reason; 0 unless the mention clearly
    fits (rule 12): a titled word the surname at 0.8; a lone word a first, preferred or surname exactly (no
    sound-alikes); "First Last" the first name (0.85, nickname 0.9) and the surname (0.8) of the same member; a
    lowercase pair the first name exactly or as a nickname and the surname at 0.85."""
    if kind == "titled":
        s, why = _surname_score(heard_words[-1], m)
        return (s, f'after "{heard_words[0]}": {why}') if s >= 0.8 else (0.0, "")
    if len(heard_words) == 1:
        best = max(_surname_score(heard_words[0], m), _first_score(heard_words[0], m))
        return best if best[0] == 1.0 else (0.0, "")
    (f, fwhy), (l_, lwhy) = _first_score(heard_words[0], m), _surname_score(heard_words[-1], m)
    need_f, need_l = (0.9, 0.85) if kind == "pair" else (0.85, 0.8)
    if f < need_f or l_ < need_l:
        return 0.0, ""
    return (f + l_) / 2, f"{fwhy}; {lwhy}"


AGENDA_MIN = 0.75  # an agenda name "closely" spells a member's: first name and surname each at least this alike
_NOT_NAMES = pipeline.NOTE_WORDS | _TITLES


def agenda_people(tree: list[AgendaNode]) -> list[tuple[AgendaNode, int, int]]:
    """Person-name-like spans in agenda headings as (node, start, end): two or three adjacent capitalised words that
    are neither note, church-organisation nor calendar vocabulary ("Extend Sindy Jones's calling" -> "Sindy Jones").
    shortcut: one-word and titled names ("Sister Jones") are skipped; add them if agendas misspell names that way."""
    out: list[tuple[AgendaNode, int, int]] = []
    for node, _ in walk(tree):
        t, runs = node.text, list[list[tuple[int, int]]]()
        for m in pipeline._WORD.finditer(t):
            w = m.group().lower().removesuffix("'s")  # a possessive ends the name: its gap is no longer whitespace
            if not m.group()[0].isupper() or {w, evidence._stem(w)} & _NOT_NAMES:
                continue
            if runs and t[runs[-1][-1][1] : m.start()].isspace():
                runs[-1].append((m.start(), m.start() + len(w)))
            else:
                runs.append([(m.start(), m.start() + len(w))])
        out += [(node, r[0][0], r[-1][1]) for r in runs if 2 <= len(r) <= 3 and not orgs_in(t[r[0][0] : r[-1][1]])]
    return out


def _spelling(words: list[str], m: _Member) -> float:
    """How closely a written "First [Middle] Last" spells the member's name: the mean of first-name and surname
    likeness when each is at least AGENDA_MIN, else 0. Spelling only ("Bob" is a nickname of Robert, not a
    misspelling: see _written_as)."""
    first = max((sim(words[0], x) for x in (m.first, m.pref_first) if x), default=0.0)
    last = sim(words[-1], m.surname)
    return (first + last) / 2 if min(first, last) >= AGENDA_MIN else 0.0


def _written_as(words: list[str], m: _Member) -> bool:
    """The agenda writes the member's own name: "Tom Cope" for Thomas Cope (first word the first or preferred name
    or a common nickname of it, last word the surname as listed)."""
    own = {n for x in (m.first, m.pref_first) if x for n in _canon(x)}
    return words[-1] == m.surname and bool(_canon(words[0]) & own)


def _close(words: list[str], ms: list[_Member]) -> list[tuple[float, _Member]]:
    """The members a written name is (1.0, only those: a name written as one member's is no misspelling of another;
    a name as listed beats a nickname form) or closely spells, closest first."""
    written = " ".join(words)
    exact = [m for m in ms if written in (m.name.lower(), m.preferred.lower())] or [
        m for m in ms if _written_as(words, m)
    ]
    if exact:
        return [(1.0, m) for m in exact]
    return sorted(((s, m) for m in ms if (s := _spelling(words, m)) >= AGENDA_MIN), key=lambda x: -x[0])


def _agenda_matches(
    tree: list[AgendaNode], ms: list[_Member]
) -> list[tuple[AgendaNode, int, int, list[tuple[float, _Member]]]]:
    """Each agenda person name (start, end in the heading) with the members it names exactly or closely spells,
    closest first. A three-word run may start with a capitalised verb ("Meet Matt Smith"): its last two words win
    when they score at least as well, unless the whole run names a member (a middle name). A trailing plural or
    possessive "s" ("Tim Copes calling") stays outside the name when that scores better."""
    out = []
    for node, a, b in agenda_people(tree):
        t = node.text
        starts = [w.start() for w in pipeline._WORD.finditer(t, a, b)]
        spans = [(starts[1], b), (a, b)] if len(starts) == 3 else [(a, b)]
        spans += [(x, y - 1) for x, y in spans if t[y - 1] == "s"]
        found = [(x, y, _close([w.lower() for w in pipeline._WORD.findall(t, x, y)], ms)) for x, y in spans]
        if len(starts) == 3 and found[1][2][:1] and found[1][2][0][0] == 1.0:
            found.insert(0, found.pop(1))
        x, y, close = max(found, key=lambda f: f[2][0][0] if f[2] else 0.0)  # the first of equals
        out.append((node, x, y, close))
    return out


_CLEAR = 0.05  # a misspelt agenda name is fixed only towards a member clearly closer than any other


def agenda_suggestions(tree: list[AgendaNode], members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Person names in draft agenda headings that name no member (not even by a nickname) but clearly spell one
    ("Sindy Jones" -> Cindy Jones): node_id, start, end, written, candidates (the closest member: member_id, name,
    callings without dates). Suggest-only: the user decides whether to edit the heading. Nothing is offered when the
    two closest members are about as close: the agenda would decide who is meant."""
    ms = [_Member(i, d) for i, d in enumerate(members)]
    out = []
    for node, a, b, close in _agenda_matches(tree, ms):
        if not close or close[0][0] == 1.0:
            continue
        m = close[0][1]
        rival = next((s for s, x in close[1:] if x.name != m.name), 0.0)  # a duplicate row is no rival
        if round(close[0][0] - rival, 3) < _CLEAR:
            continue
        callings = [c["calling"] for c in m.d.get("callings") or []]
        out.append(
            {
                "node_id": node.id,
                "start": a,
                "end": b,
                "written": node.text[a:b],
                "candidates": [{"member_id": m.id, "name": m.preferred, "callings": callings}],
            }
        )
    return out


def agenda_links(
    tree: list[AgendaNode], members: list[dict[str, Any]], people: Iterable[tuple[str, str]] = ()
) -> dict[str, list[str]]:
    """Member display name -> agenda headings that write that member's own name (as listed, or with a nickname of
    the first name) and no other member's: where notes about that person may belong, never evidence that the item
    was discussed. A misspelt name links nobody: the draft agenda's name check offers its fix before approval.
    `people` (display name, preferred) are added when the list no longer has them (replaced or deleted since a
    confirmation)."""
    ms = [_Member(i, d) for i, d in enumerate(members)]
    known = {m.name for m in ms}
    ms += [
        _Member(len(ms) + k, {"full_name": n, "preferred_name": p}) for k, (n, p) in enumerate(people) if n not in known
    ]
    out: dict[str, list[str]] = {}
    for node, _, _, close in _agenda_matches(tree, ms):
        if close and close[0][0] == 1.0 and len({m.name for _, m in close}) == 1:  # one person, or no link
            headings = out.setdefault(close[0][1].name, [])
            if node.text not in headings:
                headings.append(node.text)
    return out


def _heading_marks(tree: list[AgendaNode], text: str) -> tuple[list[int], list[str]]:
    """Positions in the transcript where a content word of an agenda heading occurs, with the heading path at each."""
    paths: dict[str, str] = {}
    stack: list[str] = []
    by_stem: dict[str, list[str]] = {}
    for node, depth in walk(tree):
        stack[depth:] = [" ".join(node.text.split())]
        paths[node.id] = " › ".join(stack)
        for st in pipeline._topic_words(node.text, text):
            by_stem.setdefault(st, []).append(node.id)
    marks: list[tuple[int, str]] = []
    for m in pipeline._WORD.finditer(text):
        for nid in by_stem.get(evidence._stem(m.group().lower()), []):
            marks.append((m.start(), paths[nid]))
    marks.sort()
    return [p for p, _ in marks], [t for _, t in marks]


def suggest(
    transcript: str,
    tree: list[AgendaNode],
    members: list[dict[str, Any]],
    decisions: Iterable[dict[str, Any]] = (),
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Ranked member suggestions for name-like mentions in the transcript, one entry per distinct heard text.

    `members`: dicts with id (optional), preferred_name, full_name, spouse_name, callings (as parse_members_csv
    returns). `decisions`: the user's earlier answers ({"heard", "name", "member_id", "status"}): a confirmed heard
    text is not suggested again, a rejected (heard, member) pair is dropped. Each entry: heard, start, count (how
    often the heard text occurs: a confirmation applies to every occurrence), quotes (the transcript lines around up
    to three occurrences), agenda_item (heading path it seems to sit under, context only), orgs (organisations
    discussed nearby), candidates (top `limit`: member_id, full_name, name, preferred, callings, reason, score).
    Candidates rank by name likeness; context (agenda item, organisation, spouse, title) only orders equal names. A
    member whose name the agenda writes exactly or closely is ranked by the better of its likeness to the member's
    spelling and to the agenda's, the latter never above the agenda's own likeness to the member: the agenda never
    decides who is meant. Only an agenda writing the member's own name (or a nickname form) counts as context."""
    ms = [_Member(i, d) for i, d in enumerate(members)]
    if not ms or not transcript:
        return []
    ids = {m.id for m in ms}
    confirmed = {d["heard"].lower() for d in decisions if d["status"] == "confirmed"}
    # by member id; by name only when that member is gone (the list was replaced since)
    rejected = {
        (d["heard"].lower(), d.get("member_id") if d.get("member_id") in ids else d["name"])
        for d in decisions
        if d["status"] == "rejected"
    }
    surnames = Counter(m.surname for m in ms)
    exact = {x.lower() for m in ms for x in (m.name, m.preferred)}
    starts = {n for m in ms for x in (m.first, m.pref_first) if x for n in _canon(x)}
    # member id -> (spelling score, name as the agenda writes it): context and a second spelling, never evidence
    agenda_as: dict[str, tuple[float, str]] = {}
    for node, a, b, close in _agenda_matches(tree, ms):
        for sc_, m in close:
            if sc_ == close[0][0] and sc_ > agenda_as.get(m.id, (0.0, ""))[0]:  # only the members it spells best
                agenda_as[m.id] = (sc_, node.text[a:b])
    as_written = {w: _Member(0, {"full_name": w}) for _, w in agenda_as.values()}
    starts |= {w.split()[0].lower() for w in as_written}
    starts |= {k for k, v in NICKNAMES.items() if v & starts}
    mark_pos, mark_path = _heading_marks(tree, transcript)
    best: dict[str, dict[str, Any]] = {}  # heard key -> suggestion being built
    name_scores: dict[tuple[str, str], list[tuple[float, str, _Member]]] = {}
    own: dict[str, set[tuple[int, int]]] = {}  # heard key -> its mentions that are not part of a longer one
    for s, e, heard, kind in _mentions(transcript, starts):
        key = " ".join(heard.lower().split())
        if key in exact or key in confirmed:
            continue
        if key not in own:
            own[key] = set(pipeline.bare_mentions(heard, transcript))
        if (s, e) not in own[key]:  # "Ross" in "Sister Ross": a confirmation of "Ross" would not cover it
            continue
        if (key, kind) not in name_scores:  # the name part does not depend on where the mention occurs
            words = [w.lower() for w in pipeline._WORD.findall(heard)]
            name_scores[key, kind] = []
            for m in ms:
                if (key, m.id) in rejected or (key, m.name) in rejected:
                    continue
                sc, why = _score(words, kind, m)
                if m.id in agenda_as:  # "sharon lomy" may be closer to the agenda's "Sharon Levi" than to the member
                    asc, w = agenda_as[m.id]
                    sa = min(_score(words, kind, as_written[w])[0], asc)  # 1.0 only for the member's own name
                    if sa > sc and sa >= AGENDA_MIN:
                        sc, why = sa, f'"{key}" {"matches" if sa == 1.0 else "sounds like"} the agenda\'s spelling'
                if sc >= MIN_SCORE:
                    name_scores[key, kind].append((sc, why, m))
        scored = name_scores[key, kind]
        if not scored:
            continue
        around = transcript[max(0, s - _WINDOW) : s] + "\n" + transcript[e : e + _WINDOW]  # the mention itself excluded
        k = bisect.bisect_right(mark_pos, s) - 1
        item = mark_path[k] if k >= 0 and s - mark_pos[k] <= _HEADING_REACH else None
        under, near = orgs_in(item or ""), orgs_in(around, capitalised=True)
        here = under | near
        title = key.split()[0].rstrip(".") if kind == "titled" else ""
        couple = {x.lower() for _, _, m in scored for x in (m.name, m.preferred)}
        sug = best.setdefault(key, {"heard": " ".join(heard.split()), "spans": [], "_c": {}})
        sug["spans"].append((s, e))
        for sc, why, m in scored:
            reasons, ctx = [why], 0.0
            # The organisation of the agenda item the mention sits under counts more than one merely mentioned nearby.
            shared = sorted(set(m.orgs) & under) or sorted(set(m.orgs) & near)
            if shared:
                ctx += 0.25 if shared[0] in under else 0.1
                c = m.orgs[shared[0]]
                where = f'agenda item "{item}"' if shared[0] in under else "the discussion nearby"
                reasons.append(f"{calling_text(c)}; {shared[0]} is mentioned in {where}")
            # "Sister Ross ... Mary": when the spouse could be the one meant, the spouse's name nearby tells nothing.
            spouse_near = m.spouse_first and re.search(rf"\b{re.escape(m.spouse_first)}\b", around, re.I)
            if spouse_near and not (title and m.spouse.lower() in couple):
                ctx += 0.1
                reasons.append(f"spouse {m.spouse} is mentioned nearby")
            if m.id in agenda_as:  # context only for the member's own name; a close spelling is display text only
                same, w = agenda_as[m.id][0] == 1.0, agenda_as[m.id][1]
                ctx += 0.2 if same else 0.0
                reasons.append(
                    f'On the agenda as "{w}"' if same else f'may be on the agenda as "{w}" (spelled differently)'
                )
            mismatch = bool(title in _TITLE_GENDER and m.gender and m.gender != _TITLE_GENDER[title])
            if mismatch:
                reasons.append(f'the listed callings suggest this is not a "{title}"')
            rank = (round(sc, 3), not mismatch, round(ctx, 3))
            prev = sug["_c"].get(m.id)
            if prev is None or rank > prev["_rank"]:
                sug["_c"][m.id] = {
                    "member_id": m.id,
                    "full_name": m.d["full_name"],
                    "name": m.name,
                    "preferred": m.preferred,
                    "callings": m.callings,
                    "reason": "; ".join(reasons),
                    "score": round(sc, 3),
                    "_rank": rank,
                    "_surname": m.surname,
                    "_ctx": (s, e, item, here),
                }
    out = []
    for sug in best.values():
        cands = sorted(sug.pop("_c").values(), key=lambda c: (tuple(-x for x in c["_rank"]), c["name"]))[:limit]
        s, e, item, here = cands[0]["_ctx"]
        for c in cands:
            if sum(x["score"] == c["score"] and x["_surname"] == c["_surname"] for x in cands) > 1:
                c["reason"] += f"; {surnames[c['_surname']]} members share this surname"
        cands = [{k: v for k, v in c.items() if not k.startswith("_")} for c in cands]
        spans = [(s, e)] + [x for x in dict.fromkeys(sug.pop("spans")) if x != (s, e)]
        sug.update(
            start=min(x for x, _ in spans),
            count=len(own[" ".join(sug["heard"].lower().split())]),
            quotes=[evidence.context(transcript, a, b, 120) for a, b in spans[:3]],
            agenda_item=item,
            orgs=sorted(here),
            candidates=cands,
        )
        out.append(sug)
    return sorted(out, key=lambda x: x["start"])
