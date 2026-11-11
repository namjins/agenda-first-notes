"""Regression tests from the adversarial data-integrity/history review. SYNTHETIC data only."""

from __future__ import annotations

import json

import pytest

from tests.conftest import finding, node_ids, process_sync, read, run_meeting, ward_council_handler

WC = "ward_council"


def _ws():  # type: ignore[no-untyped-def]
    from agenda_first_notes.workspaces import get_workspace

    return get_workspace(WC)


def _review(client, mid):  # type: ignore[no-untyped-def]
    return client.get(f"/api/w/{WC}/meetings/{mid}/review").json()


def _note(client, mid, prefix):  # type: ignore[no-untyped-def]
    return next(n for n in _review(client, mid)["notes"] if n["text"].startswith(prefix) and not n["deleted"])


def _approve_all(client, mid) -> None:  # type: ignore[no-untyped-def]
    for f in _review(client, mid)["findings"]:
        client.post(f"/api/w/{WC}/meetings/{mid}/findings/{f['id']}/resolve")
    client.post(f"/api/w/{WC}/meetings/{mid}/approve").raise_for_status()


def _wc(client):  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    return mid


def _tree(client, mid):  # type: ignore[no-untyped-def]
    return client.get(f"/api/w/{WC}/meetings/{mid}").json()["agenda"]["tree"]


def _node_id(tree, text):  # type: ignore[no-untyped-def]
    for n in tree:
        if n["text"] == text:
            return n["id"]
        found = _node_id(n.get("children", []), text)
        if found:
            return found
    return None


# ------------------------------------------------------------------ processing races (findings 1, 2)


def test_edits_refused_while_processing_and_review_never_clobbers_user_rows(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import services
    from agenda_first_notes.models import Note
    from agenda_first_notes.workspaces import session_for

    mid = _wc(client)
    target = _note(client, mid, "Decided to adopt")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{target['id']}", json={"text": "User text v1."}).raise_for_status()
    seen: dict[str, object] = {}

    def handler(req):  # type: ignore[no-untyped-def]
        if req.purpose == "review" and "refused" not in seen:
            for call in (
                lambda: services.edit_note(_ws(), mid, target["id"], {"text": "v2"}),
                lambda: services.set_transcript(_ws(), mid, "SYNTHETIC other", source="correction"),
                lambda: services.set_agenda(_ws(), mid, text="Only", source="text"),
                lambda: services.approve_agenda(_ws(), mid),
                lambda: services.delete_meeting(_ws(), mid),
            ):
                with pytest.raises(services.ServiceError):
                    call()
            seen["refused"] = True
            with session_for(_ws()) as s:  # a write that slipped in anyway must not be overwritten
                row = s.get(Note, target["id"])
                assert row is not None
                row.text, row.deleted = "Written during processing.", True
                s.commit()
        return ward_council_handler(req)

    fake_provider.handler = handler
    process_sync(WC, mid)
    assert seen["refused"]
    n = next(x for x in _review(client, mid)["notes"] if x["id"] == target["id"])
    assert (n["text"], n["deleted"]) == ("Written during processing.", True)


def test_job_error_path_survives_state_moved_on(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes.models import Meeting
    from agenda_first_notes.workspaces import session_for

    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))

    def handler(req):  # type: ignore[no-untyped-def]
        with session_for(_ws()) as s:
            m = s.get(Meeting, mid)
            assert m is not None
            m.state = "transcript_added"
            s.commit()
        raise RuntimeError("SYNTHETIC boom")

    fake_provider.handler = handler
    with pytest.raises(RuntimeError, match="SYNTHETIC boom"):  # the original error, not a transition error
        process_sync(WC, mid)
    m = client.get(f"/api/w/{WC}/meetings/{mid}").json()
    assert m["state"] == "transcript_added" and "SYNTHETIC boom" in (m["last_error"] or "")


# ------------------------------------------------------------------ provenance and status (3, c, d)


def test_hedged_note_is_not_a_confirmed_decision(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    assert _note(client, mid, "Activity days will be moved.")["status"] == "needs_review"
    _approve_all(client, mid)
    d = next(x for x in client.get(f"/api/w/{WC}/decisions").json() if x["text"] == "Activity days will be moved.")
    assert d["status"] == "proposed"
    adopted = next(x for x in client.get(f"/api/w/{WC}/decisions").json() if x["text"].startswith("Decided to adopt"))
    assert adopted["status"] == "confirmed"


def test_moved_note_is_user_edited_keeps_checks_and_transcript_provenance(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    tree = _tree(client, mid)
    rowan = _note(client, mid, "Rowan Castellano")
    assert rowan["status"] == "verified"
    r = client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"agenda_node_id": _node_id(tree, "Ministering")}
    )
    assert r.json()["status"] == "user_edited" and r.json()["origin"] == "user"
    # The overconfident note, only moved, still gets the model-note checks.
    hedged = _note(client, mid, "Activity days will be moved.")
    client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{hedged['id']}", json={"agenda_node_id": _node_id(tree, "Closing")}
    ).raise_for_status()
    findings = _review(client, mid)["findings"]
    assert any(
        f["note_id"] == hedged["id"] and f["category"] == "overconfident" and f["round_no"] == 99 for f in findings
    )
    _approve_all(client, mid)
    fu = next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["description"].startswith("Rowan"))
    assert fu["origin"] == "transcript"


def test_owner_whitespace_flattened_and_owner_edit_is_user_content(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    rowan = _note(client, mid, "Rowan Castellano")
    r = client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"owner": " Rowan \n Castellano "})
    assert r.json()["owner"] == "Rowan Castellano" and r.json()["status"] == "user_edited"
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"owner": None}).raise_for_status()
    _approve_all(client, mid)
    fu = next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["description"].startswith("Rowan"))
    assert fu["origin"] == "user"


# ------------------------------------------------------------------ regeneration (a, b)


def test_regeneration_does_not_duplicate_history(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)

    def counts():  # type: ignore[no-untyped-def]
        return tuple(len(client.get(f"/api/w/{WC}/{p}").json()) for p in ("follow-ups", "decisions"))

    first = counts()
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    process_sync(WC, mid)  # new note ids
    _approve_all(client, mid)
    assert counts() == first
    assert all(f["status"] == "proposed" for f in client.get(f"/api/w/{WC}/follow-ups").json())


def test_regeneration_respects_moved_and_reworded_notes(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    tree = _tree(client, mid)
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"agenda_node_id": _node_id(tree, "Ministering")}
    ).raise_for_status()
    adopt = _note(client, mid, "Decided to adopt")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{adopt['id']}", json={"text": "Meal rotation adopted."})
    process_sync(WC, mid)
    live = [n for n in _review(client, mid)["notes"] if not n["deleted"] and n["status"] != "unsupported"]
    assert [n["agenda_node_id"] for n in live if n["text"].startswith("Rowan")] == [_node_id(tree, "Ministering")]
    assert not any(n["text"].startswith("Decided to adopt") for n in live)


# ------------------------------------------------------------------ agenda changes (4, coverage)


def test_agenda_change_detaches_notes_resets_coverage_and_skips_history(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"text": "Rowan Castellano drafts the list."})
    tree = _tree(client, mid)

    def drop(nodes):  # type: ignore[no-untyped-def]
        return [{**x, "children": drop(x.get("children", []))} for x in nodes if x["id"] != rowan["agenda_node_id"]]

    # Two saves before approval: only the second removes the node.
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"tree": tree, "source": "edit"}).raise_for_status()
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"tree": drop(tree), "source": "edit"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    assert _review(client, mid)["coverage"] is None
    assert next(n for n in _review(client, mid)["notes"] if n["id"] == rowan["id"])["agenda_node_id"] is None
    process_sync(WC, mid)
    _approve_all(client, mid)
    assert not [f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["source_note_id"] == rowan["id"]]


# ------------------------------------------------------------------ approval (5, also-seen)


def test_untagged_follow_up_after_reopen_becomes_unverified(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    temple = _note(client, mid, "May or June")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{temple['id']}", json={"follow_up": False}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/approve").raise_for_status()
    fu = next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["source_note_id"] == temple["id"])
    assert fu["status"] == "unverified" and fu["events"][-1]["new_status"] == "unverified"


def test_ready_for_approval_blocks_on_fresh_high_findings(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    temple = _note(client, mid, "May or June")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{temple['id']}", json={"agenda_node_id": None}).raise_for_status()
    assert client.post(f"/api/w/{WC}/meetings/{mid}/approve").status_code == 400


# ------------------------------------------------------------------ audit and meeting edits (6, 7)


def test_new_note_audit_uses_real_id(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    nid = client.post(f"/api/w/{WC}/meetings/{mid}/notes", json={"text": "SYNTHETIC manual note"}).json()["id"]
    audit = client.get(f"/api/w/{WC}/audit", params={"meeting_id": mid}).json()
    assert [a["entity_id"] for a in audit if a["entity_type"] == "note"] == [nid]


def test_meeting_fields_locked_after_approval_and_audited_before(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    client.patch(f"/api/w/{WC}/meetings/{mid}", json={"meeting_date": "2026-03-08"}).raise_for_status()
    audit = client.get(f"/api/w/{WC}/audit", params={"meeting_id": mid}).json()
    upd = next(a for a in audit if a["action"] == "updated")
    assert (upd["prev"]["meeting_date"], upd["new"]["meeting_date"]) == ("2026-03-01", "2026-03-08")
    _approve_all(client, mid)
    assert client.patch(f"/api/w/{WC}/meetings/{mid}", json={"meeting_date": "2027-01-01"}).status_code == 400
    assert client.patch(f"/api/w/{WC}/meetings/{mid}", json={"title": "Changed"}).status_code == 400
    assert client.get(f"/api/w/{WC}/meetings/{mid}").json()["meeting_date"] == "2026-03-08"


# ------------------------------------------------------------------ history inbox (8, 9, 10)


def test_event_date_changes_never_fork(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import history
    from agenda_first_notes.models import ScheduledEvent
    from agenda_first_notes.workspaces import session_for

    with session_for(_ws()) as s:
        e = ScheduledEvent(title="SYNTHETIC trip", event_date="2027-05-01", origin="transcript", evidence=[])
        s.add(e)
        s.commit()
        eid = e.id
    a = history.set_event_date(_ws(), eid, "2027-05-08")
    b = history.set_event_date(_ws(), eid, "2027-05-15")  # submitted from the stale row
    assert b.supersedes_id == a.id
    evs = history.list_scheduled_events(_ws())
    assert len([x for x in evs if not any(y.supersedes_id == x.id for y in evs)]) == 1
    with pytest.raises(history.ServiceError):
        history.set_event_date(_ws(), eid, "  ")


def test_follow_up_note_with_unchanged_status_is_kept(client) -> None:  # type: ignore[no-untyped-def]
    fu = client.post(f"/api/w/{WC}/follow-ups", json={"description": "SYNTHETIC item"}).json()
    client.patch(f"/api/w/{WC}/follow-ups/{fu['id']}", json={"status": "open", "note": "SYNTHETIC check"})
    f = next(x for x in client.get(f"/api/w/{WC}/follow-ups").json() if x["id"] == fu["id"])
    assert "SYNTHETIC check" in [e["note"] for e in f["events"]]


def test_user_edits_to_history_are_not_labelled_transcript(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes.models import CallingEvent
    from agenda_first_notes.workspaces import session_for

    with session_for(_ws()) as s:
        c = CallingEvent(event_type="suggested", note="SYNTHETIC", origin="transcript", evidence=[])
        s.add(c)
        s.commit()
        cid = c.id
    assert client.patch(f"/api/w/{WC}/calling-events/{cid}", json={"event_type": "sustained"}).status_code == 400
    r = client.patch(f"/api/w/{WC}/calling-events/{cid}", json={"person": "Synthetic Person"}).json()
    assert (r["event_type"], r["origin"]) == ("suggested", "user")
    mid = _wc(client)
    _approve_all(client, mid)
    fu = next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["origin"] == "transcript")
    assert client.patch(f"/api/w/{WC}/follow-ups/{fu['id']}", json={"owner": "Someone Else"}).json()["origin"] == "user"


# ------------------------------------------------------------------ carry-forward (e)


def test_carry_forward_can_be_unticked_and_render_filters(client) -> None:  # type: ignore[no-untyped-def]
    item = client.post(f"/api/w/{WC}/follow-ups", json={"description": "Ask neighbors about parking"}).json()
    mid = run_meeting(client, WC, "Ask neighbors about parking\nOther", "SYNTHETIC nothing said")
    client.patch(f"/api/w/{WC}/meetings/{mid}", json={"carry_forward": [item["id"]]}).raise_for_status()
    client.patch(f"/api/w/{WC}/follow-ups/{item['id']}", json={"status": "completed"}).raise_for_status()
    tail = _review(client, mid)["markdown"].split("Next Meeting – Follow-Up Items")[1]
    assert "parking" not in tail
    r = client.patch(f"/api/w/{WC}/meetings/{mid}", json={"carry_forward": [item["id"]]})
    assert r.status_code == 200 and r.json()["carry_forward"] == []
    assert client.patch(f"/api/w/{WC}/meetings/{mid}", json={"carry_forward": [item["id"]]}).status_code == 400


# ------------------------------------------------------------------ delete (f, privacy)


def test_delete_meeting_is_really_gone_but_keeps_tracked_follow_ups(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    marker = "ZQSYNTHETICMARKERQZ"
    transcript = read("ward_council_transcript.txt") + f"\nSpeaker 1: {marker} closing remark."
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), transcript)
    process_sync(WC, mid)
    _approve_all(client, mid)
    fus = client.get(f"/api/w/{WC}/follow-ups").json()
    tracked, untouched = fus[0], fus[1]
    client.patch(f"/api/w/{WC}/follow-ups/{tracked['id']}", json={"status": "open"}).raise_for_status()
    # Another meeting lists the untouched item on its agenda and carries it forward.
    other = run_meeting(client, WC, f"{untouched['description']}\nOther", "SYNTHETIC nothing said")
    client.patch(f"/api/w/{WC}/meetings/{other}", json={"carry_forward": [untouched["id"]]}).raise_for_status()

    assert client.delete(f"/api/w/{WC}/meetings/{mid}").status_code == 204
    left = {f["id"]: f for f in client.get(f"/api/w/{WC}/follow-ups").json()}
    assert set(left) == {tracked["id"]}
    kept = left[tracked["id"]]
    assert (kept["meeting_id"], kept["source_note_id"], kept["evidence"]) == (None, None, [])
    assert all(e["meeting_id"] is None and e["evidence"] == [] for e in kept["events"])
    assert client.get(f"/api/w/{WC}/meetings/{other}").json()["carry_forward"] == []
    audit = [a for a in client.get(f"/api/w/{WC}/audit", params={"limit": 1000}).json() if a["meeting_id"] == mid]
    assert audit and all(a["prev"] is None and a["new"] is None for a in audit)
    gone = next(a for a in client.get(f"/api/w/{WC}/audit").json() if a["action"] == "deleted")
    assert "title" not in (gone["prev"] or {})
    ws = _ws()
    blob = b"".join(p.read_bytes() for p in ws.db_path.parent.glob(ws.db_path.name + "*"))
    assert marker.encode() not in blob


def test_restart_recovery_fails_meeting_stuck_in_processing(client) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import jobs
    from agenda_first_notes.models import Job, Meeting
    from agenda_first_notes.workspaces import session_for

    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    with session_for(_ws()) as s:
        s.get(Meeting, mid).state = "processing"  # type: ignore[union-attr]
        s.add(Job(meeting_id=mid, kind="process", status="running", message="Running"))
        s.commit()
    assert jobs.recover_interrupted(_ws()) == 1
    m = client.get(f"/api/w/{WC}/meetings/{mid}").json()
    assert m["state"] == "failed" and "restart" in m["last_error"]
    assert client.delete(f"/api/w/{WC}/meetings/{mid}").status_code == 204


# ------------------------------------------------------------------ review I (r1-r9 and follow-ups)


def _live(client, mid):  # type: ignore[no-untyped-def]
    return [n for n in _review(client, mid)["notes"] if not n["deleted"] and n["status"] != "unsupported"]


def _twin_handler(req):  # type: ignore[no-untyped-def]
    """Same wording on two different agenda items: two distinct follow-ups."""
    if req.purpose == "review":
        return json.dumps({"findings": []})
    ids = node_ids(req.prompt)
    same = "Follow up at the next meeting."
    return json.dumps(
        {
            "findings": [
                finding(
                    ids.get("Temple trip"),
                    same,
                    ["Let's think about it."],
                    category="follow_up_candidate",
                    follow_up=True,
                ),
                finding(
                    ids.get("Youth Conference planning"),
                    same,
                    ["we haven't settled on anything"],
                    category="follow_up_candidate",
                    follow_up=True,
                ),
            ]
        }
    )


def test_same_wording_on_two_agenda_items_gives_two_history_rows(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    fake_provider.handler = _twin_handler
    mid = _wc(client)
    assert len([n for n in _live(client, mid) if n["follow_up"]]) == 2
    _approve_all(client, mid)
    assert sorted(f["agenda_topic"] for f in client.get(f"/api/w/{WC}/follow-ups").json()) == [
        "Temple trip",
        "Youth Conference planning",
    ]
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    process_sync(WC, mid)  # new note ids: each row is re-matched to the note on its own agenda item
    _approve_all(client, mid)
    fus = client.get(f"/api/w/{WC}/follow-ups").json()
    assert len(fus) == 2 and all(f["status"] == "proposed" for f in fus)


def test_deleting_a_note_does_not_suppress_same_wording_elsewhere(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    fake_provider.handler = _twin_handler
    mid = _wc(client)
    tree = _tree(client, mid)
    temple = next(n for n in _live(client, mid) if n["agenda_node_id"] == _node_id(tree, "Temple trip"))
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{temple['id']}", json={"deleted": True}).raise_for_status()
    process_sync(WC, mid)
    places = [n["agenda_node_id"] for n in _live(client, mid)]
    assert places == [_node_id(tree, "Youth Conference planning")]


def test_reviewer_add_does_not_resurrect_reworded_or_moved_notes(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    tree = _tree(client, mid)
    csp = _node_id(tree, "Compassionate service plan")
    adopt = _note(client, mid, "Decided to adopt")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{adopt['id']}", json={"text": "SYNTHETIC user wording only."})
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"agenda_node_id": _node_id(tree, "Ministering")}
    ).raise_for_status()

    def add(text, quotes):  # type: ignore[no-untyped-def]
        return {
            "note_id": None,
            "severity": "high",
            "category": "omission",
            "message": "SYNTHETIC missing",
            "action": "add",
            "replacement_text": text,
            "new_agenda_node_id": csp,
            "evidence": quotes,
        }

    def handler(req):  # type: ignore[no-untyped-def]
        if req.purpose == "review":
            adds = [
                add(
                    "Decided to adopt the meal rotation starting next month.",
                    ["we decided to adopt the meal rotation starting next month"],
                ),
                add(
                    "Rowan Castellano will draft the rotation list by the fifteenth.",
                    ["Rowan Castellano, can you draft the rotation list?"],
                ),
            ]
            return json.dumps({"findings": adds})
        return ward_council_handler(req)

    fake_provider.handler = handler
    process_sync(WC, mid)
    live = _live(client, mid)
    assert not any(n["text"].startswith("Decided to adopt") for n in live)
    assert [n["agenda_node_id"] for n in live if n["text"].startswith("Rowan")] == [_node_id(tree, "Ministering")]


def test_withdrawn_decision_becomes_unverified_and_returns(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)

    def decision(prefix):  # type: ignore[no-untyped-def]
        return next(x for x in client.get(f"/api/w/{WC}/decisions").json() if x["text"].startswith(prefix))

    user_set = decision("Activity days")
    client.patch(f"/api/w/{WC}/decisions/{user_set['id']}", json={"status": "confirmed"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    adopt = _note(client, mid, "Decided to adopt")
    hedged = _note(client, mid, "Activity days")
    for n in (adopt, hedged):
        client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{n['id']}", json={"deleted": True}).raise_for_status()
    _approve_all(client, mid)
    assert decision("Decided to adopt")["status"] == "unverified"
    assert decision("Activity days")["status"] == "confirmed"  # the user's own inbox decision stands
    audit = client.get(f"/api/w/{WC}/audit", params={"meeting_id": mid}).json()
    assert any(a["action"] == "not_in_approved_notes" and a["entity_type"] == "decision" for a in audit)
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{adopt['id']}", json={"deleted": False}).raise_for_status()
    _approve_all(client, mid)
    assert decision("Decided to adopt")["status"] == "confirmed"


def test_retagged_follow_up_returns_to_proposed(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)
    temple = _note(client, mid, "May or June")
    for flag in (False, True):
        client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
        client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{temple['id']}", json={"follow_up": flag}).raise_for_status()
        _approve_all(client, mid)
    fu = next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["source_note_id"] == temple["id"])
    assert fu["status"] == "proposed"
    assert [(e["prev_status"], e["new_status"]) for e in fu["events"]][-2:] == [
        ("proposed", "unverified"),
        ("unverified", "proposed"),
    ]


def test_first_event_date_is_the_users_and_forks_follow_newest_branch(client) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import history
    from agenda_first_notes.models import ScheduledEvent
    from agenda_first_notes.workspaces import session_for

    with session_for(_ws()) as s:
        e = ScheduledEvent(title="SYNTHETIC trip", event_date=None, origin="transcript", evidence=[])
        s.add(e)
        s.commit()
        eid = e.id
    assert history.set_event_date(_ws(), eid, "2027-05-08").origin == "user"
    with session_for(_ws()) as s:  # a fork left by older code
        older = ScheduledEvent(title="SYNTHETIC trip", event_date="2027-06-01", supersedes_id=eid, origin="user")
        s.add(older)
        s.commit()
        newer = ScheduledEvent(title="SYNTHETIC trip", event_date="2027-06-02", supersedes_id=eid, origin="user")
        s.add(newer)
        s.commit()
        newer_id = newer.id
    assert history.set_event_date(_ws(), eid, "2027-07-01").supersedes_id == newer_id


def test_delete_purges_approved_indexed_text_and_agenda_images(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes.models import AgendaImage
    from agenda_first_notes.workspaces import session_for

    marker = "ZQSYNTHETICMARKERQZ"
    mid = _wc(client)
    adopt = _note(client, mid, "Decided to adopt")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{adopt['id']}", json={"text": f"Decided {marker} rotation."})
    with session_for(_ws()) as s:
        s.add(AgendaImage(meeting_id=mid, filename="x.png", sha256="0", extracted_text=f"img {marker}"))
        s.commit()
    _approve_all(client, mid)
    assert client.delete(f"/api/w/{WC}/meetings/{mid}").status_code == 204
    ws = _ws()
    assert marker.encode() not in b"".join(p.read_bytes() for p in ws.db_path.parent.glob(ws.db_path.name + "*"))


def test_cancelled_or_failed_regeneration_keeps_previous_notes(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import services

    mid = _wc(client)
    before = sorted(n["id"] for n in _live(client, mid))
    assert before
    with pytest.raises(services.Cancelled):
        services.process_meeting(_ws(), mid, should_cancel=lambda: True)
    assert sorted(n["id"] for n in _live(client, mid)) == before

    def down(req):  # type: ignore[no-untyped-def]
        from agenda_first_notes.llm.base import ProviderError

        raise ProviderError("SYNTHETIC model unavailable")

    fake_provider.handler = down
    with pytest.raises(services.ServiceError):
        process_sync(WC, mid)
    assert sorted(n["id"] for n in _live(client, mid)) == before
    assert client.get(f"/api/w/{WC}/meetings/{mid}").json()["state"] == "failed"


def test_another_process_starting_never_fails_a_live_job(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    """`agenda-first-notes migrate` or a second `serve` while this server processes a meeting."""
    import subprocess
    import sys
    from pathlib import Path

    from agenda_first_notes import services
    from agenda_first_notes.models import Job
    from agenda_first_notes.workspaces import session_for

    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    with session_for(_ws()) as s:
        s.add(Job(meeting_id=mid, kind="process", status="running", message="Running"))
        s.commit()
    seen: dict[str, object] = {}

    def handler(req):  # type: ignore[no-untyped-def]
        if req.purpose == "review" and "ran" not in seen:
            code = "from agenda_first_notes import main; main.init_storage(); main.recover_jobs()"  # a 2nd serve
            seen["ran"] = subprocess.run(
                [sys.executable, "-c", code], cwd=Path(__file__).parents[1], capture_output=True, text=True
            )
            with pytest.raises(services.ServiceError):
                services.delete_meeting(_ws(), mid)
        return ward_council_handler(req)

    fake_provider.handler = handler
    process_sync(WC, mid)
    assert seen["ran"].returncode == 0, seen["ran"].stderr  # type: ignore[attr-defined]
    with session_for(_ws()) as s:
        assert [j.status for j in s.query(Job).filter(Job.meeting_id == mid)] == ["running"]


def test_job_outcome_write_retries_and_never_leaves_meeting_processing(client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import OperationalError

    from agenda_first_notes import jobs
    from agenda_first_notes.models import Job, Meeting
    from agenda_first_notes.workspaces import session_for

    calls: list[int] = []

    def flaky() -> None:
        calls.append(1)
        if len(calls) < 3:
            raise OperationalError("UPDATE jobs", {}, Exception("database is locked"))

    monkeypatch.setattr(jobs.time, "sleep", lambda _s: None)
    jobs._retry(flaky)
    assert len(calls) == 3

    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))

    def runner(ws, job, report, should_cancel):  # type: ignore[no-untyped-def]
        with session_for(ws) as s:  # a runner whose own error handling could not record the outcome
            s.get(Meeting, mid).state = "processing"  # type: ignore[union-attr]
            s.commit()
        raise RuntimeError("SYNTHETIC crash")

    jobs.register("zz_test", runner)
    with session_for(_ws()) as s:
        j = Job(meeting_id=mid, kind="zz_test", status="queued")
        s.add(j)
        s.commit()
        jid = j.id
    jobs._run(WC, jid)
    with session_for(_ws()) as s:
        assert s.get(Job, jid).status == "failed"  # type: ignore[union-attr]
        assert s.get(Meeting, mid).state == "failed"  # type: ignore[union-attr]


def test_resolving_findings_is_refused_when_locked_and_audited(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    f = next(x for x in _review(client, mid)["findings"] if not x["resolved"])
    client.post(f"/api/w/{WC}/meetings/{mid}/findings/{f['id']}/resolve").raise_for_status()
    audit = client.get(f"/api/w/{WC}/audit", params={"meeting_id": mid}).json()
    assert any(a["entity_type"] == "review_finding" and a["entity_id"] == f["id"] for a in audit)
    _approve_all(client, mid)
    assert (
        client.post(f"/api/w/{WC}/meetings/{mid}/findings/{f['id']}/resolve", json={"resolved": False}).status_code
        == 400
    )


# ------------------------------------------------------------------ review J


def _legacy(note_id: str) -> None:
    """A note touched before migration 0004: source_node_id was never recorded."""
    from agenda_first_notes.models import Note
    from agenda_first_notes.workspaces import session_for

    with session_for(_ws()) as s:
        s.get(Note, note_id).source_node_id = None  # type: ignore[union-attr]
        s.commit()


def test_legacy_reworded_and_moved_notes_still_suppress_their_original(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    tree = _tree(client, mid)
    adopt = _note(client, mid, "Decided to adopt")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{adopt['id']}", json={"text": "Meal rotation adopted."})
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"agenda_node_id": _node_id(tree, "Ministering")}
    ).raise_for_status()
    _legacy(adopt["id"])
    _legacy(rowan["id"])
    process_sync(WC, mid)
    texts = [n["text"] for n in _live(client, mid)]
    assert "Meal rotation adopted." in texts and not any(t.startswith("Decided to adopt") for t in texts)
    assert len([t for t in texts if t.startswith("Rowan")]) == 1


def test_unchanged_agenda_text_reapproved_keeps_every_note_mapped(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    before = {n["id"]: n["agenda_node_id"] for n in _live(client, mid)}
    ids = {n["id"] for n, _ in _walk(_tree(client, mid))}
    text = read("ward_council_agenda.txt").replace("Welcome and", "Welcome  and")  # whitespace only
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": text, "source": "text"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    assert {n["id"] for n, _ in _walk(_tree(client, mid))} == ids
    after = {n["id"]: n["agenda_node_id"] for n in _review(client, mid)["notes"] if n["id"] in before}
    assert after == before and all(before.values())


def _walk(nodes, depth=0):  # type: ignore[no-untyped-def]
    for n in nodes:
        yield n, depth
        yield from _walk(n.get("children", []), depth + 1)


def test_deleted_claim_stays_hidden_when_its_heading_moves(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    bad = _note(client, mid, "Activity days will be moved")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{bad['id']}", json={"deleted": True}).raise_for_status()
    old_id = bad["agenda_node_id"]
    # Same heading moved to the top level: its old id cannot be reused, only its text links it.
    text = read("ward_council_agenda.txt").replace("    Activity days schedule\n", "") + "Activity days schedule\n"
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": text, "source": "text"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    assert _node_id(_tree(client, mid), "Activity days schedule") != old_id
    process_sync(WC, mid)
    assert not any(n["text"].startswith("Activity days will be moved") for n in _live(client, mid))


def test_edit_after_regeneration_keeps_the_history_link(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    _approve_all(client, mid)
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    process_sync(WC, mid)  # new note ids: rows are taken over by claim and relinked
    _approve_all(client, mid)
    count = len(client.get(f"/api/w/{WC}/follow-ups").json())
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}",
        json={"text": "Rowan Castellano will draft the meal rotation list by the fifteenth."},
    ).raise_for_status()
    _approve_all(client, mid)
    fus = client.get(f"/api/w/{WC}/follow-ups").json()
    assert len(fus) == count and any(f["source_note_id"] == rowan["id"] for f in fus)


def test_unverified_calling_event_returns_despite_user_note_edit(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    def handler(req):  # type: ignore[no-untyped-def]
        out = ward_council_handler(req)
        if req.purpose not in ("review", "agenda"):
            d = json.loads(out)
            d["findings"].append(
                finding(
                    node_ids(req.prompt).get("Ministering"),
                    "SYNTHETIC calling was suggested.",
                    ["Interviews are mostly done, we're at about eighty percent."],
                    category="calling_event",
                    calling_event="suggested",
                )
            )
            out = json.dumps(d)
        return out

    fake_provider.handler = handler
    mid = _wc(client)
    _approve_all(client, mid)

    def events():  # type: ignore[no-untyped-def]
        out = client.get(f"/api/w/{WC}/calling-events").json()
        return out["events"] if isinstance(out, dict) else out

    eid = next(c["id"] for c in events() if (c["note"] or "").startswith("SYNTHETIC calling"))

    def event():  # type: ignore[no-untyped-def]
        return next(c for c in events() if c["id"] == eid)

    note = _note(client, mid, "SYNTHETIC calling")
    for deleted in (True, False):
        client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
        client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{note['id']}", json={"deleted": deleted}).raise_for_status()
        _approve_all(client, mid)
        if deleted:
            assert event()["status"] == "unverified"
            client.patch(f"/api/w/{WC}/calling-events/{eid}", json={"note": "SYNTHETIC reviewed"}).raise_for_status()
    assert event()["status"] is None


def test_recovery_only_in_the_serving_process_and_never_of_its_own_jobs(client) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import jobs
    from agenda_first_notes.main import init_storage
    from agenda_first_notes.models import Job
    from agenda_first_notes.workspaces import session_for

    ws = _ws()
    with session_for(ws) as s:
        stale = Job(kind="zz_noop", status="running", message="Running")
        s.add(stale)
        s.commit()
    init_storage()  # what `migrate` runs: no recovery
    with session_for(ws) as s:
        assert s.get(Job, stale.id).status == "running"  # type: ignore[union-attr]
    # This server did not own the lock (another process held it); now it is free: the next job takes it over.
    jobs._owned.pop(ws.root / "worker.lock").close()
    jobs.register("zz_noop", lambda *_a: None)
    mine = jobs.enqueue(ws, "zz_noop", None)
    jobs._executor.submit(lambda: None).result(timeout=30)  # single worker: the queue has drained
    with session_for(ws) as s:
        assert s.get(Job, stale.id).status == "failed"  # type: ignore[union-attr]
        assert s.get(Job, mine.id).status == "done"  # type: ignore[union-attr]
    assert ws.root / "worker.lock" in jobs._owned


# ------------------------------------------------------------------ review K (one-to-one claim passes)


def _filed_under(target, prefixes, when):  # type: ignore[no-untyped-def]
    """Model that, once `when()` is true, files notes starting with `prefixes` under the item named `target`."""

    def handler(req):  # type: ignore[no-untyped-def]
        out = ward_council_handler(req)
        ids = node_ids(req.prompt)
        if req.purpose in ("review", "agenda") or not when() or target not in ids:
            return out
        d = json.loads(out)
        for f in d["findings"]:
            if f["text"].startswith(prefixes):
                f["agenda_node_id"] = ids[target]
        return json.dumps(d)

    return handler


def _rowan_fus(client):  # type: ignore[no-untyped-def]
    return [f for f in client.get(f"/api/w/{WC}/follow-ups").json() if "Rowan" in f["description"]]


def test_deleted_note_stays_hidden_when_the_model_files_it_elsewhere(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    state = {"moved": False}
    fake_provider.handler = _filed_under("Relief Society", ("Rowan",), lambda: state["moved"])
    mid = _wc(client)
    rowan = _note(client, mid, "Rowan Castellano")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"deleted": True}).raise_for_status()
    state["moved"] = True
    process_sync(WC, mid)
    assert not [n for n in _live(client, mid) if n["text"].startswith("Rowan")]


def test_history_row_follows_a_claim_the_model_files_elsewhere(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    state = {"moved": False}
    fake_provider.handler = _filed_under("Relief Society", ("Rowan",), lambda: state["moved"])
    mid = _wc(client)
    _approve_all(client, mid)
    fu = _rowan_fus(client)[0]
    client.patch(f"/api/w/{WC}/follow-ups/{fu['id']}", json={"status": "open"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    state["moved"] = True
    process_sync(WC, mid)
    _approve_all(client, mid)
    assert [(f["id"], f["status"]) for f in _rowan_fus(client)] == [(fu["id"], "open")]


def test_leaf_heading_rename_keeps_one_history_row(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    old, new = "Compassionate service plan", "Compassionate service plan for meals"
    fake_provider.handler = _filed_under(new, ("Rowan", "Decided"), lambda: True)
    mid = _wc(client)
    _approve_all(client, mid)
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    text = read("ward_council_agenda.txt").replace(old, new)
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": text, "source": "text"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    process_sync(WC, mid)
    _approve_all(client, mid)
    assert len(_rowan_fus(client)) == 1
    decided = [d for d in client.get(f"/api/w/{WC}/decisions").json() if d["text"].startswith("Decided to adopt")]
    assert len(decided) == 1


def _find(tree, path):  # type: ignore[no-untyped-def]
    nodes, node = tree, None
    for p in path:
        node = next(x for x in nodes if x["text"] == p)
        nodes = node.get("children", [])
    return node["id"]  # type: ignore[index]


def test_removed_groups_generic_child_id_is_not_reused_by_a_new_group(client) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import services

    mid = run_meeting(client, WC, "Relief Society\n  Updates\nElders Quorum\n  Updates\nClosing\n", "SYNTHETIC text.")
    rs_updates = _find(_tree(client, mid), ["Relief Society", "Updates"])
    n = services.edit_note(_ws(), mid, None, {"text": "SYNTHETIC relief society note.", "agenda_node_id": rs_updates})
    new = "Elders Quorum\n  Updates\nPrimary\n  Updates\nClosing\n"
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": new, "source": "text"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    tree = _tree(client, mid)
    assert _find(tree, ["Primary", "Updates"]) != rs_updates
    assert next(x for x in _review(client, mid)["notes"] if x["id"] == n.id)["agenda_node_id"] is None


def test_reimport_after_an_unapproved_draft_reuses_the_approved_ids(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = _wc(client)
    base = read("ward_council_agenda.txt")
    before = {n["id"]: n["agenda_node_id"] for n in _review(client, mid)["notes"]}
    typo = base.replace("Temple trip", "Temple trp")
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": typo, "source": "text"}).raise_for_status()
    client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"text": base, "source": "text"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/agenda/approve").raise_for_status()
    assert {n["id"]: n["agenda_node_id"] for n in _review(client, mid)["notes"]} == before


def test_a_second_copy_refuses_jobs_instead_of_queueing_them(client) -> None:  # type: ignore[no-untyped-def]
    import subprocess
    import sys
    from pathlib import Path

    from agenda_first_notes import jobs, services
    from agenda_first_notes.models import Job
    from agenda_first_notes.workspaces import session_for

    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    script = (
        "import sys; from agenda_first_notes import jobs, services; from agenda_first_notes.workspaces import "
        "get_workspace\ntry:\n    jobs.enqueue(get_workspace('ward_council'), 'process', sys.argv[1])\n"
        "except services.ServiceError as e:\n    print('refused', e)"
    )
    b = subprocess.run(
        [sys.executable, "-c", script, mid], cwd=Path(__file__).parents[1], capture_output=True, text=True, timeout=30
    )
    assert b.stdout.startswith("refused Another running copy"), b.stderr  # and it exits promptly: no wait loop
    with session_for(_ws()) as s:
        assert not s.query(Job).filter(Job.meeting_id == mid).all()  # nothing left queued to block this copy
    # Here: this copy loses the lock to another holder, then refuses too; once free it takes over and enqueues.
    jobs._owned.pop(_ws().root / "worker.lock").close()
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; from agenda_first_notes import jobs; from agenda_first_notes.workspaces import "
            "get_workspace; print(jobs.own_workers(get_workspace('ward_council')), flush=True); input()",
        ],
        cwd=Path(__file__).parents[1],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert holder.stdout is not None and holder.stdout.readline().strip() == "True"
    with pytest.raises(services.ServiceError, match="Another running copy"):
        jobs.enqueue(_ws(), "zz_never", mid)
    holder.communicate("\n", timeout=30)
    jobs.register("zz_ok", lambda *_a: None)
    assert jobs.enqueue(_ws(), "zz_ok", mid).meeting_id == mid


def test_deleted_discussion_marker_never_suppresses_another_items_marker(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import render

    state = {"run": 1}

    def handler(req):  # type: ignore[no-untyped-def]
        out = ward_council_handler(req)
        if req.purpose in ("review", "agenda"):
            return out
        ids = node_ids(req.prompt)
        d = json.loads(out)
        disc = [{"agenda_node_id": ids["Closing"], "evidence": ["Okay, closing. Thanks everyone."]}]
        if state["run"] == 1:
            disc.append(
                {
                    "agenda_node_id": ids["Previous follow-up: Trunk-or-treat parking"],
                    "evidence": ["someone should ask the neighbors"],
                }
            )
        d["discussed"] = disc
        return json.dumps(d)

    fake_provider.handler = handler
    mid = _wc(client)
    trunk = _node_id(_tree(client, mid), "Previous follow-up: Trunk-or-treat parking")
    marker = next(
        n
        for n in _review(client, mid)["notes"]
        if n["text"] == render.NO_CLEAR_OUTCOME and n["agenda_node_id"] == trunk
    )
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{marker['id']}", json={"deleted": True}).raise_for_status()
    state["run"] = 2
    process_sync(WC, mid)
    closing = _review(client, mid)["markdown"].split("- Closing")[1].split("\n- ")[0]
    assert "Not discussed." not in closing and render.NO_CLEAR_OUTCOME in closing


def test_row_from_a_reworded_note_is_still_downgraded_when_withdrawn(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    def handler(req):  # type: ignore[no-untyped-def]
        out = ward_council_handler(req)
        if req.purpose not in ("review", "agenda"):
            d = json.loads(out)
            d["findings"].append(
                finding(
                    node_ids(req.prompt).get("Ministering"),
                    "SYNTHETIC calling was suggested.",
                    ["Interviews are mostly done, we're at about eighty percent."],
                    category="calling_event",
                    calling_event="suggested",
                )
            )
            out = json.dumps(d)
        return out

    fake_provider.handler = handler
    mid = _wc(client)
    note = _note(client, mid, "SYNTHETIC calling")
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{note['id']}", json={"text": "SYNTHETIC calling suggested."})
    _approve_all(client, mid)

    def event():  # type: ignore[no-untyped-def]
        out = client.get(f"/api/w/{WC}/calling-events").json()
        return next(c for c in (out["events"] if isinstance(out, dict) else out) if c["source_note_id"] == note["id"])

    assert event()["origin"] == "user" and event()["status"] is None
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{note['id']}", json={"deleted": True}).raise_for_status()
    _approve_all(client, mid)
    assert event()["status"] == "unverified"
