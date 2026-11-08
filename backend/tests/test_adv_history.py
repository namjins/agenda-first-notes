"""Regression tests from the adversarial data-integrity/history review. SYNTHETIC data only."""

from __future__ import annotations

import pytest

from tests.conftest import process_sync, read, run_meeting, ward_council_handler

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
