"""Decision Rooms through FastAPI: who opens, who decides, and what each role sees."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app, store
    from tests.helpers import signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

QUESTION = "Should we file a SAR for Alpha Trading Ltd?"
OPTIONS = ["File SAR draft for signature", "Request more evidence", "Close as legitimate transfer"]


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class RoomsApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.zone = "rooms-" + os.urandom(3).hex()
        self.op_zone = self.zone + "-ops"
        self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": "Case files",
                                                                        "read_roles": ["approver", "viewer"]}).status_code, 201)
        self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": self.op_zone, "name": "Operations",
                                                                        "read_roles": ["operator"]}).status_code, 201)
        self.addCleanup(self._clean)

    def _clean(self):
        store.delete_zone(self.zone)
        store.delete_zone(self.op_zone)

    def _open(self, client=None, **kw):
        body = {"question": QUESTION, "zone": self.zone, "options": OPTIONS, "case": "AML-2026-0412", **kw}
        return (client or self.admin).post("/api/v1/rooms", json=body)

    def test_a_room_is_opened_gains_evidence_and_is_decided(self):
        r = self._open()
        self.assertEqual(r.status_code, 201, r.text)
        room = r.json()
        self.assertEqual([o["label"] for o in room["options"]], OPTIONS)
        self.assertEqual((room["status"], room["opened_by_kind"], room["waiting_for"]), ("open", "person", ["anyone who may decide"]))

        f = self.admin.post("/api/v1/content/files", json={"zone": self.zone, "name": "board minutes.pdf", "classification": "confidential"}).json()
        ev = self.admin.post(f"/api/v1/rooms/{room['id']}/evidence",
                             json={"kind": "file", "ref": f["id"], "label": "Board minutes, 28 Sep", "verdict": "Not verifiable"})
        self.assertEqual(ev.status_code, 201, ev.text)
        self.assertEqual(ev.json()["counts"]["evidence"], 1)
        self.assertEqual(self.admin.post(f"/api/v1/rooms/{room['id']}/evidence",
                                         json={"kind": "output", "ref": "out_nope", "label": "x"}).status_code, 404)
        self.assertEqual(self.admin.post(f"/api/v1/rooms/{room['id']}/evidence",
                                         json={"kind": "note", "label": "A note", "verdict": "Maybe"}).status_code, 422)

        approver = signed_in("approver")
        seen = approver.get(f"/api/v1/rooms/{room['id']}").json()
        self.assertEqual((seen["may_decide"], seen["counts"]["evidence"]), (True, 1))
        decided = approver.post(f"/api/v1/rooms/{room['id']}/decide",
                                json={"option": "request-more-evidence", "rationale": "The board minutes are not verified yet."})
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual((decided.json()["status"], decided.json()["outcome"]["agreed"]), ("decided", True))
        again = approver.post(f"/api/v1/rooms/{room['id']}/decide", json={"option": "request-more-evidence", "rationale": "Once more, please."})
        self.assertEqual(again.status_code, 409)
        self.assertIn("decided", again.json()["detail"])
        audit = self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
        self.assertTrue(any(e["action"] == "room.decided" and e["target"] == room["id"] for e in audit))

    def test_a_second_approver_must_decide_too(self):
        second = user("approver")
        room = self._open(second_approver=second).json()
        first = signed_in("approver")
        self.assertEqual(first.post(f"/api/v1/rooms/{room['id']}/decide",
                                    json={"option": "file-sar-draft-for-signature", "rationale": "Layering is consistent."}).status_code, 200)
        still = first.get(f"/api/v1/rooms/{room['id']}").json()
        self.assertEqual((still["status"], still["waiting_for"]), ("open", [second]))
        other = signed_in("approver")
        self.assertEqual(other.post(f"/api/v1/rooms/{room['id']}/decide",
                                    json={"option": "request-more-evidence", "rationale": "I want the minutes checked."}).status_code, 409)
        closed = signed_in("approver", second).post(f"/api/v1/rooms/{room['id']}/decide",
                                                   json={"option": "request-more-evidence", "rationale": "Minutes first, please."})
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertEqual((closed.json()["status"], closed.json()["outcome"]["agreed"]), ("decided", False))
        self.assertEqual(self._open(second_approver="nobody@example.org").status_code, 422)

    def test_who_opens_who_decides_and_who_only_reads(self):
        room = self._open().json()
        self.assertEqual(self._open(client=signed_in("operator"), zone=self.op_zone).status_code, 201)  # operators open rooms
        self.assertEqual(self._open(client=signed_in("operator")).status_code, 404)  # but not into a zone they cannot read
        self.assertEqual(self._open(client=signed_in("approver"), zone=self.op_zone).status_code, 403)  # approvers decide, not open
        viewer = signed_in("viewer")
        seen = viewer.get(f"/api/v1/rooms/{room['id']}").json()
        self.assertEqual((seen["may_decide"], seen["reason"]), (False, "Decisions are recorded by an Admin or an Approver."))
        self.assertEqual(viewer.post(f"/api/v1/rooms/{room['id']}/decide",
                                     json={"option": "request-more-evidence", "rationale": "I would like more."}).status_code, 403)
        operator = signed_in("operator")  # not a role on this zone: the room is simply not there
        self.assertEqual(operator.get(f"/api/v1/rooms/{room['id']}").status_code, 404)
        self.assertEqual([x for x in operator.get("/api/v1/rooms").json() if x["id"] == room["id"]], [])
        mine = signed_in("approver").get("/api/v1/rooms", params={"mine": "true"}).json()
        self.assertIn(room["id"], [x["id"] for x in mine])

    def test_an_agent_opens_a_room_and_files_findings(self):
        inst = "room-inst-" + os.urandom(3).hex()
        pair = self.admin.post("/api/v1/instances", json={"id": inst, "environment": "production", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        agent = {"Authorization": f"Bearer {tok}", "X-Hermes-Profile": "case-orchestrator"}
        bare = TestClient(app)
        r = bare.post(f"/agent/v1/instances/{inst}/rooms",
                      json={"question": QUESTION, "zone": self.zone, "options": OPTIONS, "case": "AML-2026-0412"}, headers=agent)
        self.assertEqual(r.status_code, 201, r.text)
        room_id = r.json()["id"]
        f = bare.post(f"/agent/v1/rooms/{room_id}/findings",
                      json={"agent": "ownership-tracer", "text": "Four entities share a beneficial owner.",
                            "verdict": "Evidence found", "run_id": "run_9"}, headers=agent)
        self.assertEqual(f.status_code, 201, f.text)
        room = self.admin.get(f"/api/v1/rooms/{room_id}").json()
        self.assertEqual((room["opened_by"], room["opened_by_kind"]), ("case-orchestrator", "agent"))
        self.assertEqual([(x["agent"], x["verdict"]) for x in room["findings"]], [("ownership-tracer", "Evidence found")])
        self.assertEqual(bare.post(f"/agent/v1/rooms/{room_id}/findings",
                                   json={"agent": "x", "text": "y", "verdict": "Probably"}, headers=agent).status_code, 422)

    def test_an_analytical_finding_is_checked_against_the_session_not_the_agents_word(self):
        inst = "room-inst-" + os.urandom(3).hex()
        pair = self.admin.post("/api/v1/instances", json={"id": inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        agent = {"Authorization": f"Bearer {tok}", "X-Hermes-Profile": "sas-analyst"}
        bare = TestClient(app)
        room_id = bare.post(f"/agent/v1/instances/{inst}/rooms", json={"question": QUESTION, "zone": self.zone, "options": OPTIONS},
                            headers=agent).json()["id"]
        store.add_events(inst, [{"kind": "tool.post", "session_id": "s1", "tool": "mcp_sas_viya__run_model"}])
        file = lambda **kw: bare.post(f"/agent/v1/rooms/{room_id}/findings", json={"agent": "sas-analyst", **kw}, headers=agent)
        self.assertEqual(file(text="PD is 4.2% under the stressed scenario.", basis="analytical", tool="sas-viya.run_model",
                              session_id="s1").status_code, 201)
        self.assertEqual(file(text="The score says low risk.", basis="analytical", tool="sas-viya.score", session_id="s1").status_code, 201)
        self.assertEqual(file(text="Rates stay flat next year.", basis="assumption").status_code, 201)
        self.assertEqual(file(text="Approve it.", basis="judgment").status_code, 422)
        self.assertEqual(file(text="A number.", basis="analytical").status_code, 422)
        found = self.admin.get(f"/api/v1/rooms/{room_id}").json()["findings"]
        self.assertEqual([(f["basis"], (f["tool_check"] or {}).get("verdict")) for f in found],
                         [("analytical", "Evidence found"), ("analytical", "No evidence"), ("assumption", None)])

        r = self.admin.post(f"/api/v1/rooms/{room_id}/evidence", json={"kind": "note", "label": "Rates assumption is ours", "basis": "assumption"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["evidence"][-1]["basis"], "assumption")
        self.assertEqual(self.admin.post(f"/api/v1/rooms/{room_id}/evidence",
                                         json={"kind": "note", "label": "x", "basis": "analytical"}).status_code, 422)

    def test_cancelling_a_room(self):
        room = self._open().json()
        other_admin = signed_in("admin")
        self.assertEqual(other_admin.post(f"/api/v1/rooms/{room['id']}/cancel").status_code, 200)  # an Admin may
        self.assertEqual(self.admin.post(f"/api/v1/rooms/{room['id']}/cancel").status_code, 409)  # not twice
        opened_by_operator = self._open(client=signed_in("operator"), zone=self.op_zone).json()
        # another Operator reads the same zone, but the room is not theirs to cancel
        self.assertEqual(signed_in("operator").post(f"/api/v1/rooms/{opened_by_operator['id']}/cancel").status_code, 403)


if __name__ == "__main__":
    unittest.main()
