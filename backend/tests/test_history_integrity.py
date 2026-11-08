"""Findings from the adversarial history/accuracy review, pinned as tests."""

from __future__ import annotations

import json

from tests.conftest import finding, node_ids, process_sync, read, run_meeting

WC = "ward_council"


def _approve_all(client, mid: str) -> None:  # type: ignore[no-untyped-def]
    for f in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["findings"]:
        client.post(f"/api/w/{WC}/meetings/{mid}/findings/{f['id']}/resolve")
    client.post(f"/api/w/{WC}/meetings/{mid}/approve").raise_for_status()


def test_reapproval_does_not_duplicate_history(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    _approve_all(client, mid)
    counts = lambda: (  # noqa: E731
        len(client.get(f"/api/w/{WC}/follow-ups").json()),
        len(client.get(f"/api/w/{WC}/decisions").json()),
    )
    first = counts()
    client.post(f"/api/w/{WC}/meetings/{mid}/reopen").raise_for_status()
    # Even after the user renamed a follow-up in the inbox, re-approval must not re-propose it.
    fu = client.get(f"/api/w/{WC}/follow-ups").json()[0]
    client.patch(f"/api/w/{WC}/follow-ups/{fu['id']}", json={"description": "Renamed by user"}).raise_for_status()
    client.post(f"/api/w/{WC}/meetings/{mid}/approve").raise_for_status()
    assert counts() == first
    assert len(client.get(f"/api/w/{WC}/meetings/{mid}/notes/revisions").json()) == 2


def test_carry_forward_only_accepts_undiscussed_agenda_items(client) -> None:  # type: ignore[no-untyped-def]
    done = client.post(f"/api/w/{WC}/follow-ups", json={"description": "Completed unrelated task"}).json()
    client.patch(f"/api/w/{WC}/follow-ups/{done['id']}", json={"status": "completed"})
    open_item = client.post(f"/api/w/{WC}/follow-ups", json={"description": "Ask neighbors about parking"}).json()
    mid = run_meeting(client, WC, "Ask neighbors about parking\nOther", "SYNTHETIC nothing said")
    r = client.patch(f"/api/w/{WC}/meetings/{mid}", json={"carry_forward": [done["id"]]})
    assert r.status_code == 400  # Case C / completed item cannot be carried forward
    r = client.patch(f"/api/w/{WC}/meetings/{mid}", json={"carry_forward": [open_item["id"]]})
    assert r.status_code == 200  # manually created follow-up (no source meeting) is a valid Case B candidate
    review = client.get(f"/api/w/{WC}/meetings/{mid}/review").json()
    assert review["undiscussed_follow_ups"][0]["follow_up_id"] == open_item["id"]
    assert "- Ask neighbors about parking" in review["markdown"].split("Next Meeting – Follow-Up Items")[1]


def test_user_deletion_and_untagging_survive_regeneration(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    notes = {n["text"]: n for n in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["notes"]}
    rowan = notes["Rowan Castellano will draft the rotation list by the fifteenth."]
    temple = notes["May or June proposed for the temple trip; no decision reached."]
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{rowan['id']}", json={"deleted": True}).raise_for_status()
    client.patch(f"/api/w/{WC}/meetings/{mid}/notes/{temple['id']}", json={"follow_up": False}).raise_for_status()
    process_sync(WC, mid)
    md = client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["markdown"]
    assert "Rowan Castellano will draft" not in md
    tail = md.split("Next Meeting – Follow-Up Items")[1]
    assert "temple trip" not in tail.lower()


def test_delete_meeting_removes_its_history_and_audits(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    mid = run_meeting(client, WC, read("ward_council_agenda.txt"), read("ward_council_transcript.txt"))
    process_sync(WC, mid)
    _approve_all(client, mid)
    assert client.get(f"/api/w/{WC}/follow-ups").json()
    assert client.delete(f"/api/w/{WC}/meetings/{mid}").status_code == 204
    assert client.get(f"/api/w/{WC}/follow-ups").json() == []
    assert client.get(f"/api/w/{WC}/decisions").json() == []
    audit = client.get(f"/api/w/{WC}/audit").json()
    deleted = next(a for a in audit if a["action"] == "deleted")
    assert deleted["prev"]["history_rows"]["follow_ups"]


def test_dedupe_keeps_contradictory_and_distinct_claims(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    def handler(req):  # type: ignore[no-untyped-def]
        if req.purpose != "extract":
            return json.dumps({"findings": []})
        ids = node_ids(req.prompt)
        n = ids.get("Budget")
        return json.dumps(
            {
                "findings": [
                    finding(
                        n,
                        "Budget approved for the youth activity.",
                        ["budget approved for the youth activity"],
                        category="decision",
                    ),
                    finding(
                        n,
                        "Budget not approved for the adult activity.",
                        ["budget not approved for the adult activity"],
                        category="decision",
                    ),
                    finding(
                        n,
                        "Contact Casey Morgan about the primary roster.",
                        ["contact Casey Morgan about the primary roster"],
                        category="assignment",
                        follow_up=True,
                    ),
                    finding(
                        n,
                        "Contact Casey Morgan about the nursery roster.",
                        ["contact Casey Morgan about the nursery roster"],
                        category="assignment",
                        follow_up=True,
                    ),
                    finding(
                        n,
                        "Sister Example suggested for Primary president.",
                        ["Sister Example suggested for Primary president"],
                        category="calling_event",
                        calling_event="suggested",
                    ),
                    finding(
                        n,
                        "Sister Example approved for Primary president.",
                        ["Sister Example approved for Primary president"],
                        category="calling_event",
                        calling_event="approved_to_proceed",
                    ),
                ]
            }
        )

    fake_provider.handler = handler
    t = "SYNTHETIC: budget approved for the youth activity; budget not approved for the adult activity; contact Casey Morgan about the primary roster; contact Casey Morgan about the nursery roster; Sister Example suggested for Primary president; Sister Example approved for Primary president."
    mid = run_meeting(client, WC, "Budget", t)
    process_sync(WC, mid)
    texts = [n["text"] for n in client.get(f"/api/w/{WC}/meetings/{mid}/review").json()["notes"] if not n["deleted"]]
    assert len(texts) == 6


def test_unmapped_follow_up_blocks_and_is_not_rendered(client, fake_provider) -> None:  # type: ignore[no-untyped-def]
    fake_provider.handler = lambda req: (
        json.dumps(
            {
                "findings": [
                    finding(None, "Someone will call the vendor.", ["someone will call the vendor"], follow_up=True)
                ]
            }
        )
        if req.purpose == "extract"
        else json.dumps({"findings": []})
    )
    mid = run_meeting(client, WC, "Only topic", "SYNTHETIC someone will call the vendor")
    res = process_sync(WC, mid)
    review = client.get(f"/api/w/{WC}/meetings/{mid}/review").json()
    assert res["state"] == "review_required"
    assert "vendor" not in review["markdown"]
    assert any(f["category"] == "unmapped" and f["severity"] == "high" for f in review["findings"])
