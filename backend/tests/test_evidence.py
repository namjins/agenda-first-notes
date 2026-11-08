from agenda_first_notes.evidence import (
    locate,
    looks_overconfident,
    owner_supported,
    revalidate,
    same_claim,
    validate_quotes,
)


def test_locate_exact_and_whitespace_tolerant() -> None:
    text = "Speaker 1: we decided to adopt\n  the meal   rotation starting next month."
    assert locate("we decided to adopt", text) == (11, 30)
    s, e = locate("adopt the meal rotation", text, offset=100)  # type: ignore[misc]
    assert text[s - 100 : e - 100].startswith("adopt") and text[s - 100 : e - 100].endswith("rotation")
    assert locate("invented quotation", text) is None
    assert locate("we", text) is None  # too short to count as evidence


def test_validate_rejects_invented_and_keeps_offsets() -> None:
    text = "Pat: I'll look into the bus quote."
    ok, bad = validate_quotes(["I'll look into the bus quote", "the bishop prayed"], text, 50, "tv1")
    assert bad == ["the bishop prayed"]
    assert ok[0]["start"] == 55 and ok[0]["quote"] == "I'll look into the bus quote"
    assert revalidate(ok, "changed text entirely", "tv2") == []
    moved = revalidate(ok, "Intro. " + text, "tv2")
    assert moved[0]["quote"] == ok[0]["quote"] and moved[0]["start"] == ok[0]["start"] - 50 + 7


def test_owner_and_overconfidence_checks() -> None:
    ev = [{"quote": "Rowan Castellano, can you draft it? Yes, I'll draft it."}]
    assert owner_supported("Rowan Castellano", ev)
    assert not owner_supported("Avery Lindqvist", ev)
    assert owner_supported(None, [])
    assert looks_overconfident(
        "Activity days will be moved.",
        "decision",
        "low",
        [{"quote": "we may need to move it, we'll see"}],
    )
    assert not looks_overconfident(
        "Unclear whether activity days will move.",
        "decision",
        "high",
        [{"quote": "we may need to move it"}],
    )
    assert not looks_overconfident(
        "Decided to adopt the meal rotation.",
        "decision",
        "low",
        [{"quote": "we decided to adopt the meal rotation"}],
    )
    assert looks_overconfident("The calling was approved.", "calling_event", "high", [{"quote": "what about Jordan?"}])


def test_same_claim() -> None:
    assert same_claim("Draft the rotation list.", "draft the rotation list")
    assert not same_claim("Draft the rotation list.", "Book the bus for youth conference")


def test_reviewer_cannot_dispute_a_name_present_in_verified_evidence() -> None:
    from agenda_first_notes.pipeline import ReviewItem, _disputes_present_name

    note = {"evidence": [{"quote": "Rowan Castellano, can you draft the rotation list?"}]}
    bogus = ReviewItem(
        note_id="x",
        severity="high",
        category="contamination",
        message="The name 'Rowan Castellano' is not in the transcript excerpt.",
        action="rewrite",
        replacement_text="A participant will draft it.",
    )
    assert _disputes_present_name(bogus, note)
    real = ReviewItem(
        note_id="x",
        severity="high",
        category="contamination",
        message="The name 'Avery Lindqvist' is not in the transcript excerpt.",
        action="rewrite",
        replacement_text="x",
    )
    assert not _disputes_present_name(real, note)
    other = ReviewItem(
        note_id="x",
        severity="high",
        category="overconfident",
        message="Rowan Castellano did not clearly accept.",
        action="rewrite",
        replacement_text="x",
    )
    assert not _disputes_present_name(other, note)
