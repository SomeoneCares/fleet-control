"""Fleet outputs through FastAPI: zone access decides who sees them, and agents file their own."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app, store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class OutputsApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.zone = "out-" + os.urandom(3).hex()
        self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": "Case files",
                                                                        "read_roles": ["approver"]}).status_code, 201)
        self.inst = "out-inst-" + os.urandom(3).hex()
        pair = self.admin.post("/api/v1/instances", json={"id": self.inst, "environment": "staging", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.addCleanup(self._clean)

    def _clean(self):
        for o in store.list_outputs([self.zone]):
            store.delete_output(o["id"])
        store.delete_zone(self.zone)

    def test_an_agent_files_an_output_and_the_right_people_see_it(self):
        r = TestClient(app).post(  # the agent has no cookie: its bearer token is the whole authentication
            f"/agent/v1/instances/{self.inst}/outputs",
            json={"zone": self.zone, "name": "Screening result 0412.json", "kind": "structured", "classification": "confidential",
                  "produced_by": "sanctions-screener", "case": "AML-2026-0412", "text": "{\"match_count\": 0}",
                  "source": {"kind": "agent", "ref": "run_9"}},
            headers=self.agent)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["provenance"], "sanctions-screener produced it from an agent run (run_9) on " + self.inst)

        rows = self.admin.get("/api/v1/outputs", params={"zone": self.zone}).json()
        self.assertEqual([(o["name"], o["classification"]) for o in rows], [("Screening result 0412.json", "confidential")])
        self.assertEqual(self.admin.get("/api/v1/outputs", params={"case": "AML-2026-0412"}).json()[0]["id"], rows[0]["id"])
        self.assertEqual(self.admin.get(f"/api/v1/outputs/{rows[0]['id']}").json()["text"], "{\"match_count\": 0}")

        approver = signed_in("approver")  # the zone's role
        self.assertIn(rows[0]["id"], [o["id"] for o in approver.get("/api/v1/outputs", params={"zone": self.zone}).json()])
        self.assertIn(rows[0]["id"], [o["id"] for o in approver.get("/api/v1/outputs").json()])  # and without a zone filter
        operator = signed_in("operator")  # not on this zone
        self.assertEqual(operator.get("/api/v1/outputs", params={"zone": self.zone}).json(), [])
        self.assertEqual(operator.get(f"/api/v1/outputs/{rows[0]['id']}").status_code, 404)

        audit = self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
        self.assertTrue(any(e["action"] == "output.produced" and self.zone in e["target"] for e in audit))

    def test_a_person_files_one_and_can_remove_it(self):
        r = self.admin.post("/api/v1/outputs", json={"zone": self.zone, "name": "Weekly summary.md", "kind": "markdown",
                                                     "text": "Two open cases."})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["source"], {"kind": "upload"})
        self.assertIn("produced it from an upload", r.json()["provenance"])
        self.assertEqual(signed_in("viewer").post("/api/v1/outputs", json={"zone": self.zone, "name": "x"}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/outputs", json={"zone": "nope", "name": "x"}).status_code, 404)
        self.assertEqual(self.admin.post("/api/v1/outputs", json={"zone": self.zone, "name": "x", "classification": "secret"}).status_code, 422)
        self.assertEqual(self.admin.delete(f"/api/v1/outputs/{r.json()['id']}").status_code, 200)
        self.assertEqual(self.admin.delete(f"/api/v1/outputs/{r.json()['id']}").status_code, 404)
        audit = self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
        self.assertTrue(any(e["action"] == "output.saved" and "Weekly summary.md" in e["target"] for e in audit))
        self.assertTrue(any(e["action"] == "output.removed" and "Weekly summary.md" in e["target"] for e in audit))


if __name__ == "__main__":
    unittest.main()
