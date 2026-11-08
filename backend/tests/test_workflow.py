"""End-to-end workflow through the API with the fake local model."""

from __future__ import annotations

import json

import pytest

from tests.conftest import finding, node_ids, process_sync, read, run_meeting

WC = "ward_council"


def test_full_ward_council_workflow(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    m = client.get(f"/api/w/{WC}/meetings/{mid}").json()
    assert m["state"] == "transcript_added" and m["agenda_approved"]

    result = process_sync(WC, mid)
    assert result["coverage_complete"] is True
    review = client.get(f"/api/w/{WC}/meetings/{mid}/review").json()
    notes = {n["text"]: n for n in review["notes"]}
    md = review["markdown"]

    # Agenda fidelity: every heading verbatim, in order, nested.
    for line in [
        "- SYNTHETIC FIXTURE – fictional ward, fictional people",
        "- Organizations",
        "  - Relief Society",
        "    - Compassionate service plan",
        "- Previous follow-up: Trunk-or-treat parking",
        "- Closing",
    ]:
        assert line in md
    assert (
        md.index("- Ministering")
        < md.index("- Organizations")
        < md.index("- Youth Conference planning")
        < md.index("- Closing")
    )
    # Undiscussed items say so; parent with discussed children does not.
    assert "- Previous follow-up: Trunk-or-treat parking\n  - Not discussed." in md
    assert "- Organizations\n  - Relief Society\n" in md
    # Verified claims appear under the right node with tags at the end.
    assert (
        "      - Rowan Castellano will draft the rotation list by the fifteenth. [Follow-up] [Owner: Rowan Castellano]"
        in md
    )
    # Invented quote excluded entirely.
    assert "Closing prayer by the bishop." not in md
    assert notes["Closing prayer by the bishop."]["status"] == "unsupported"
    # Inferred owner stripped.
    assert notes["Theme suggested; nothing settled."]["owner"] is None
    assert "[Owner: Avery Lindqvist]" not in md
    # Overconfident claim flagged high -> review required, not ready.
    cats = {(f["category"], f["severity"]) for f in review["findings"]}
    assert ("overconfident", "high") in cats and ("inferred_owner", "low") in cats and ("unsupported", "high") in cats
    assert review["meeting"]["state"] == "review_required"
    # Flat follow-up list, deduped, no grouping.
    tail = md.split("Next Meeting – Follow-Up Items")[1].strip().splitlines()
    assert tail == [
        "- Rowan Castellano will draft the rotation list by the fifteenth. [Owner: Rowan Castellano]",
        "- May or June proposed for the temple trip; no decision reached.",
        "- Pat will look into the bus quote.",  # "Pat" alone is not a full name: owner tag withheld
    ]
    # Approval blocked while a high finding is open.
    r = client.post(f"/api/w/{WC}/meetings/{mid}/approve")
    assert r.status_code == 400 and "unresolved" in r.json()["detail"]
    # User downgrades the overconfident note -> manual edit, not transcript-verified.
    bad = notes["Activity days will be moved."]
    r = client.patch(
        f"/api/w/{WC}/meetings/{mid}/notes/{bad['id']}",
        json={
            "text": "Unclear whether activity days will move.",
            "evidence_quotes": ["we may need to move it, we'll see"],
        },
    )
    assert r.status_code == 200 and r.json()["status"] == "user_edited" and r.json()["origin"] == "user"
    r = client.post(
        f"/api/w/{WC}/meetings/{mid}/notes",
        json={"text": "Bring cookies.", "agenda_node_id": bad["agenda_node_id"]},
    )
    assert r.json()["status"] == "manual_unverified"
    for f in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["findings"]:
        if f["severity"] == "high" and not f["resolved"]:
            client.post(f"/api/w/{WC}/meetings/{mid}/findings/{f['id']}/resolve", json={"resolved": True})
    r = client.post(f"/api/w/{WC}/meetings/{mid}/approve")
    assert r.status_code == 200, r.text
    assert client.get(f"/api/w/{WC}/meetings/{mid}").json()["state"] == "approved"
    export = client.get(f"/api/w/{WC}/meetings/{mid}/export?fmt=md")
    assert (
        export.status_code == 200
        and "Unclear whether activity days will move." in export.text
        and "Bring cookies." in export.text
    )
    assert "• Ministering" in client.get(f"/api/w/{WC}/meetings/{mid}/export?fmt=txt").text
    # Editing after approval is refused until reopened; reopen keeps the approved revision.
    assert client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{bad['id']}", json={"text": "x"}).status_code == 400
    assert client.post(f"/api/w/{WC}/meetings/{mid}/reopen").json()["state"] == "ready_for_approval"
    assert len(client.get(f"/api/w/{WC}/meetings/{mid}/notes/revisions").json()) == 1
    # Historical reconciliation proposed follow-ups and decisions with evidence, as proposals.
    fus = client.get(f"/api/w/{WC}/follow-ups").json()
    assert {f["status"] for f in fus} == {"proposed"} and all(f["evidence"] for f in fus)
    decisions = client.get(f"/api/w/{WC}/decisions").json()
    statuses = {d["text"]: d["status"] for d in decisions}
    assert statuses["Decided to adopt the meal rotation starting next month."] == "confirmed"
    assert statuses["May or June proposed for the temple trip; no decision reached."] == "proposed"
    # Search finds the meeting and links to it.
    hits = client.get(f"/api/w/{WC}/search", params={"q": "rotation"}).json()
    assert hits and all(h["meeting_id"] == mid for h in hits)
    # Historical context never reached the model: prompts contain only the current transcript + agenda.
    for req in fake_provider.requests:
        assert "Jordan Okafor" not in req.prompt and "previous meeting" not in req.prompt.lower()


def test_transcript_correction_marks_notes_stale_and_versions(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    tv = client.get(f"/api/w/{WC}/meetings/{mid}/transcript").json()
    corrected = tv["text"].replace(
        "Pat, could you look into the bus quote? Pat: I'll look into the bus quote.",
        "Pat, could you look into the van quote? Pat: I'll look into the van quote.",
    )
    r = client.put(
        f"/api/w/{WC}/meetings/{mid}/transcript",
        json={"text": corrected, "reason": "Transcription error: van, not bus"},
    )
    assert r.status_code == 200 and r.json()["transcript"]["version_no"] == 2
    versions = client.get(f"/api/w/{WC}/meetings/{mid}/transcript").json()["versions"]
    assert [v["version_no"] for v in versions] == [1, 2] and versions[1]["reason"].startswith("Transcription error")
    notes = {n["text"]: n for n in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["notes"]}
    assert (
        notes["Pat will look into the bus quote."]["stale"] is True
        and notes["Pat will look into the bus quote."]["status"] == "needs_review"
    )
    assert notes["Decided to adopt the meal rotation starting next month."]["stale"] is False
    assert client.get(f"/api/w/{WC}/meetings/{mid}").json()["state"] == "transcript_added"


def test_agenda_edit_after_approval_creates_new_revision(client) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, "A\nB", "SYNTHETIC x")
    revs = client.get(f"/api/w/{WC}/meetings/{mid}/agenda/revisions").json()
    assert len(revs) == 1 and revs[0]["approved_at"]
    tree = client.get(f"/api/w/{WC}/meetings/{mid}").json()["agenda"]["tree"]
    tree[0]["text"] = "A edited"
    r = client.put(f"/api/w/{WC}/meetings/{mid}/agenda", json={"tree": tree, "source": "edit"})
    assert r.status_code == 200 and r.json()["agenda_approved"] is False
    revs = client.get(f"/api/w/{WC}/meetings/{mid}/agenda/revisions").json()
    assert len(revs) == 2 and revs[0]["tree"][0]["text"] == "A" and revs[1]["tree"][0]["text"] == "A edited"
    assert revs[1]["tree"][0]["id"] == revs[0]["tree"][0]["id"]
    assert client.post(f"/api/w/{WC}/meetings/{mid}/process").status_code == 400  # must re-approve


def agenda_free_handler(req) -> str:  # type: ignore[no-untyped-def]
    if req.purpose == "agenda":
        return json.dumps(
            {
                "topics": [
                    {"text": "Sunday School president", "children": []},
                    {"text": "Ward clerk release"},
                    {"text": "Youth fireside date"},
                ]
            }
        )
    if req.purpose == "review":
        return json.dumps({"findings": []})
    ids = node_ids(req.prompt)
    return json.dumps(
        {
            "findings": [
                finding(
                    ids.get("Sunday School president"),
                    "Jordan Okafor suggested for Sunday School president; to be considered further next week.",
                    [
                        "what about Jordan Okafor? Just a thought.",
                        "Let's pray about it and come back next week.",
                    ],
                    category="calling_event",
                    calling_event="suggested",
                    follow_up=True,
                    uncertainty="medium",
                ),
                finding(
                    ids.get("Ward clerk release"),
                    "Sam Reyes accepted the release; discussion indicated it will be presented on the nineteenth.",
                    ["Sam Reyes accepted the release", "Okay, the nineteenth."],
                    category="calling_event",
                    calling_event="accepted",
                ),
                finding(
                    ids.get("Youth fireside date"),
                    "Unclear whether the youth fireside date moved.",
                    [
                        "was it moved? I thought it was the third.",
                        "Not sure. Someone said it might move.",
                    ],
                    category="uncertainty",
                    uncertainty="high",
                    follow_up=True,
                ),
            ]
        }
    )


@pytest.mark.parametrize("fake_provider", [agenda_free_handler], indirect=True)
def test_bishopric_without_agenda_and_calling_states(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    b = "bishopric"
    mid = run_meeting(client, b, None, read("bishopric_transcript.txt"))
    r = client.post(f"/api/w/{b}/meetings/{mid}/agenda/propose")
    assert r.status_code == 200 and [t["text"] for t in r.json()["tree"]] == [
        "Sunday School president",
        "Ward clerk release",
        "Youth fireside date",
    ]
    assert client.get(f"/api/w/{b}/meetings/{mid}").json()["agenda_approved"] is False
    assert client.post(f"/api/w/{b}/meetings/{mid}/process").status_code == 400
    client.post(f"/api/w/{b}/meetings/{mid}/agenda/approve")
    res = process_sync(b, mid)
    assert res["state"] == "ready_for_approval", client.get(f"/api/w/{b}/meetings/{mid}/review").json()["findings"]
    client.post(f"/api/w/{b}/meetings/{mid}/approve").raise_for_status()
    events = client.get(f"/api/w/{b}/calling-events").json()["events"]
    types = sorted(e["event_type"] for e in events)
    assert types == ["accepted", "suggested"]  # nothing advanced: no approved/extended/sustained
    assert all(e["evidence"] for e in events)
    # Explicit admin update is allowed, audited and distinct from transcript-derived events.
    r = client.post(
        f"/api/w/{b}/calling-events",
        json={
            "person": "Sam Reyes",
            "calling": "Ward clerk",
            "event_type": "released",
            "note": "Released in sacrament meeting",
        },
    )
    assert r.status_code == 201 and r.json()["origin"] == "user"
    assert client.post(f"/api/w/{b}/calling-events", json={"event_type": "promoted"}).status_code == 400
    audit = client.get(f"/api/w/{b}/audit").json()
    assert any(a["entity_type"] == "calling_event" and a["source"] == "user" for a in audit)
    md = client.get(f"/api/w/{b}/meetings/{mid}/export").text
    assert "Unclear whether the youth fireside date moved. [Follow-up]" in md


def test_historical_follow_ups_do_not_leak_into_new_notes(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    # Meeting 1 creates follow-ups; meeting 2 lists one on the agenda but nobody discusses it (Case B),
    # and another exists only in history (Case C).
    mid1 = run_meeting(
        client,
        WC,
        read("ward_council_agenda.txt"),
        read("ward_council_transcript.txt"),
        date="2026-02-01",
    )
    process_sync(WC, mid1)
    for f in client.get(f"/api/w/{WC}/meetings/{mid1}/review").json()["findings"]:
        client.post(f"/api/w/{WC}/meetings/{mid1}/findings/{f['id']}/resolve")
    client.post(f"/api/w/{WC}/meetings/{mid1}/approve").raise_for_status()
    fus = client.get(f"/api/w/{WC}/follow-ups").json()
    bus = next(f for f in fus if "bus quote" in f["description"])
    client.patch(f"/api/w/{WC}/follow-ups/{bus['id']}", json={"status": "open"})

    fake_provider.handler = lambda req: json.dumps({"findings": []})
    agenda2 = "SYNTHETIC agenda two\nPat will look into the bus quote.\nNew topic"
    mid2 = run_meeting(
        client,
        WC,
        agenda2,
        "SYNTHETIC transcript: nothing substantive was said.",
        date="2026-03-01",
    )
    process_sync(WC, mid2)
    review = client.get(f"/api/w/{WC}/meetings/{mid2}/review").json()
    md = review["markdown"]
    assert "- Pat will look into the bus quote.\n  - Not discussed." in md
    assert md.split("Next Meeting – Follow-Up Items")[1].strip() == "No follow-up items identified."
    assert "Rowan Castellano" not in md and "temple trip" not in md.lower()  # Case C stays in history only
    case_b = review["undiscussed_follow_ups"]
    assert len(case_b) == 1 and case_b[0]["follow_up_id"] == bus["id"] and case_b[0]["status"] == "open"
    # User explicitly carries it forward -> appears in the final list; status unchanged in the inbox.
    client.patch(f"/api/w/{WC}/meetings/{mid2}", json={"carry_forward": [bus["id"]]})
    md = client.get(f"/api/w/{WC}/meetings/{mid2}/review").json()["markdown"]
    assert md.split("Next Meeting – Follow-Up Items")[1].strip() == "- Pat will look into the bus quote."
    assert next(f for f in client.get(f"/api/w/{WC}/follow-ups").json() if f["id"] == bus["id"])["status"] == "open"
    # Model prompts for meeting 2 never contained meeting-1 content.
    for req in fake_provider.requests:
        if "agenda two" in req.prompt:
            assert "Rowan" not in req.prompt and "rotation" not in req.prompt


def test_follow_up_inbox_updates_are_explicit_and_audited(client) -> None:  # type: ignore[no-untyped-def]
    f = client.post(
        f"/api/w/{WC}/follow-ups",
        json={"description": "Ask neighbors about parking", "owner": "Pat"},
    ).json()
    assert f["status"] == "open" and f["origin"] == "user"
    f = client.patch(f"/api/w/{WC}/follow-ups/{f['id']}", json={"status": "deferred", "note": "Until spring"}).json()
    assert (
        f["status"] == "deferred"
        and f["events"][-1]["new_status"] == "deferred"
        and f["events"][-1]["source"] == "user"
    )
    f = client.patch(f"/api/w/{WC}/follow-ups/{f['id']}", json={"status": "completed"}).json()
    f = client.patch(f"/api/w/{WC}/follow-ups/{f['id']}", json={"status": "open", "note": "Reopened"}).json()
    assert [e["new_status"] for e in f["events"]] == ["open", "deferred", "completed", "open"]
    assert client.patch(f"/api/w/{WC}/follow-ups/{f['id']}", json={"status": "done"}).status_code == 400
    assert len(client.get(f"/api/w/{WC}/audit").json()) >= 4


def test_event_date_change_preserves_history(client) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes.models import ScheduledEvent
    from agenda_first_notes.workspaces import get_workspace, session_for

    with session_for(get_workspace(WC)) as s:
        s.add(ScheduledEvent(id="e1", meeting_id=None, title="Youth fireside", event_date="March 3", evidence=[]))
        s.commit()
    r = client.post(f"/api/w/{WC}/events/e1/date", json={"event_date": "March 10", "note": "Moved per bishop"})
    assert r.status_code == 200 and r.json()["supersedes_id"] == "e1"
    events = {e["id"]: e for e in client.get(f"/api/w/{WC}/events").json()}
    assert events["e1"]["event_date"] == "March 3"


def test_state_machine_and_idempotent_processing(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import services
    from agenda_first_notes.workspaces import get_workspace

    ws = get_workspace(WC)
    m = services.create_meeting(ws, "t", None)
    with pytest.raises(services.ServiceError):
        services.approve_notes(ws, m.id)
    with pytest.raises(services.ServiceError):
        services.process_meeting(ws, m.id)
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    n1 = len([n for n in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["notes"]])
    process_sync(WC, mid)
    n2 = len([n for n in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["notes"]])
    assert n1 == n2  # re-run replaces, never duplicates
    with pytest.raises(services.ServiceError):
        services.approve_notes(ws, "nope")


def test_long_transcript_segments_without_loss_and_failed_segment_blocks_absence_claims(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    from agenda_first_notes import config

    s = config.load_settings()
    s.models.segment_chars, s.models.segment_overlap_chars = 2000, 200
    config.save_settings(s)
    body = "\n".join(f"SYNTHETIC line {i}: discussion about topic {i}." for i in range(300))
    seen: list[tuple[int, int]] = []

    def handler(req):  # type: ignore[no-untyped-def]
        if req.purpose == "review":
            return json.dumps({"findings": []})
        import re

        m = re.search(r"segment (\d+) of (\d+); characters (\d+)-(\d+)", req.prompt)
        assert m
        seen.append((int(m.group(3)), int(m.group(4))))
        if m.group(1) == "2":
            return "not json at all"
        return json.dumps({"findings": []})

    fake_provider.handler = handler
    mid = run_meeting(client, WC, "Topic A\nTopic B", body)
    res = process_sync(WC, mid)
    assert res["coverage_complete"] is False and res["state"] == "review_required"
    assert min(a for a, _ in seen) == 0 and max(b for _, b in seen) == len(body)
    review = client.get(f"/api/w/{WC}/meetings/{mid}/review").json()
    statuses = [seg["status"] for seg in review["coverage"]["segments"]]
    assert statuses.count("failed") == 1 and statuses.count("done") == len(statuses) - 1
    assert "Not discussed." not in review["markdown"] and "incomplete" in review["markdown"]
