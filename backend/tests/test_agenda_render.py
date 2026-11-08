from agenda_first_notes.agenda import (
    from_dicts,
    node_ids,
    outline,
    parse_text,
    to_dicts,
    to_text,
    validate_tree,
)
from agenda_first_notes.render import (
    FOLLOW_UP_HEADING,
    NO_FOLLOW_UPS,
    NOT_DISCUSSED,
    render,
    to_plain_text,
)
from agenda_first_notes.segmentation import coverage_complete, segment


def test_parse_preserves_text_order_and_nesting() -> None:
    tree = parse_text(
        "Welcome & Prayer!\nOrganizations\n  Relief Society\n    - compassionate Service, plan\n  Elders Quorum\n3. Closing"
    )
    assert [n.text for n in tree] == ["Welcome & Prayer!", "Organizations", "Closing"]
    org = tree[1]
    assert org.kind == "group" and [c.text for c in org.children] == [
        "Relief Society",
        "Elders Quorum",
    ]
    assert org.children[0].children[0].text == "compassionate Service, plan"
    assert to_text(tree).splitlines()[3] == "    compassionate Service, plan"
    ids = node_ids(tree)
    assert len(ids) == 6
    validate_tree(tree)
    assert from_dicts(to_dicts(tree))[1].children[0].id == org.children[0].id  # ids stable through round-trip
    assert "[" + org.id + "] Organizations" in outline(tree)


def test_render_exact_headings_placeholders_and_flat_follow_ups() -> None:
    tree = parse_text(
        "Opening\nOrganizations\n  Relief Society\n  Elders Quorum\nEmpty Group\n  Child A\n  Child B\nClosing"
    )
    rs = tree[1].children[0]
    notes = [
        {
            "agenda_node_id": rs.id,
            "text": "Decided to adopt the meal rotation.",
            "follow_up": False,
            "owner": None,
            "position": 0,
        },
        {
            "agenda_node_id": rs.id,
            "text": "Draft the rotation list.",
            "follow_up": True,
            "owner": "Rowan Castellano",
            "position": 1,
        },
        {
            "agenda_node_id": rs.id,
            "text": "Draft the rotation list",
            "follow_up": True,
            "owner": "Rowan Castellano",
            "position": 2,
        },  # duplicate
    ]
    md = render(
        title="Ward Council Meeting Notes",
        meeting_date="2026-03-01",
        tree=tree,
        notes=notes,
        coverage_complete=True,
    )
    assert md.startswith(
        "Ward Council Meeting Notes – March 1, 2026\n\n- Opening\n  - Not discussed.\n- Organizations\n  - Relief Society\n"
    )
    assert (
        "    - Decided to adopt the meal rotation.\n    - Draft the rotation list. [Follow-up] [Owner: Rowan Castellano]\n"
        in md
    )
    assert "  - Elders Quorum\n    - Not discussed.\n" in md
    assert "- Empty Group\n  - No discussion.\n  - Child A\n  - Child B\n" in md  # one placeholder, children kept bare
    assert md.count("**") == 0 and "|" not in md
    tail = md.split(FOLLOW_UP_HEADING)[1].strip().splitlines()
    assert tail == [
        "- Draft the rotation list. [Owner: Rowan Castellano]"
    ]  # flat, deduped, owner kept, no [Follow-up] tag
    assert (
        "No discussion." not in md.split("- Organizations")[1].split("- Empty Group")[0]
    )  # parent with discussed child gets no placeholder
    plain = to_plain_text(md)
    assert "• Opening" in plain and "- " not in plain.replace("– ", "")


def test_render_without_follow_ups_and_incomplete_coverage() -> None:
    tree = parse_text("Only Item")
    md = render(title="T", meeting_date=None, tree=tree, notes=[], coverage_complete=False)
    assert NOT_DISCUSSED not in md and "incomplete" in md
    assert md.rstrip().endswith(f"{FOLLOW_UP_HEADING}\n{NO_FOLLOW_UPS}")
    assert md.startswith("T\n\n")


def test_segmentation_covers_everything_with_overlap() -> None:
    text = "\n".join(f"Line {i}: some discussion about topic {i % 7}." for i in range(400))
    segs = segment(text, max_chars=1000, overlap=100)
    assert segs[0].start == 0 and segs[-1].end == len(text)
    for a, b in zip(segs, segs[1:], strict=False):
        assert b.start < a.end  # overlap
        assert b.start >= a.end - 100 - 1
    assert all(len(text[s.start : s.end]) <= 1000 for s in segs)
    assert not coverage_complete(segs)
    for s in segs:
        s.status = "done"
    assert coverage_complete(segs)
    assert segment("", 100) == []
