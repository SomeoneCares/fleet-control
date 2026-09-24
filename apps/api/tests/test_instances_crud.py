"""Editing and forgetting an instance: who may, what changes, and what must survive."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app, store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class InstanceCrudTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.iid = "crud-" + os.urandom(3).hex()
        r = self.admin.post("/api/v1/instances", json={"id": self.iid, "environment": "lab", "mode": "agent"})
        self.assertEqual(r.status_code, 201, r.text)
        self.pairing = r.json()["pairing_token"]
        self.addCleanup(lambda: store.delete_instance(self.iid))

    def test_an_instance_moves_between_environments_and_the_audit_says_so(self):
        r = self.admin.patch(f"/api/v1/instances/{self.iid}", json={"environment": "staging"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["environment"], "staging")
        self.assertEqual(store.get_instance(self.iid)["environment"], "staging")
        audit = self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
        entry = next(e for e in audit if e["action"] == "instance.updated" and e["target"] == self.iid)
        self.assertIn("lab", entry["detail"])       # both values, so the change is legible later
        self.assertIn("staging", entry["detail"])

    def test_what_an_edit_may_not_do(self):
        self.assertEqual(self.admin.patch(f"/api/v1/instances/{self.iid}", json={"environment": "space"}).status_code, 422)
        # the id is the agent's identity on the host: it is not editable, and an attempt changes nothing
        self.admin.patch(f"/api/v1/instances/{self.iid}", json={"id": "renamed"})
        self.assertIsNotNone(store.get_instance(self.iid))
        self.assertIsNone(store.get_instance("renamed"))
        self.assertEqual(self.admin.patch("/api/v1/instances/not-a-thing", json={"environment": "lab"}).status_code, 404)

    def test_an_unchanged_edit_is_not_written_to_the_audit(self):
        before = len([e for e in self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
                      if e["action"] == "instance.updated"])
        self.assertEqual(self.admin.patch(f"/api/v1/instances/{self.iid}", json={"environment": "lab"}).status_code, 200)
        after = len([e for e in self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
                     if e["action"] == "instance.updated"])
        self.assertEqual(before, after)

    def test_forgetting_an_instance_stops_its_agent_reporting(self):
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": self.iid, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {self.pairing}"}).json()["agent_token"]
        bare = TestClient(app)
        agent = {"Authorization": f"Bearer {tok}"}
        self.assertEqual(bare.post(f"/agent/v1/instances/{self.iid}/heartbeat",
                                   json={"agent_version": "0.1.0", "report": {}}, headers=agent).status_code, 200)
        r = self.admin.delete(f"/api/v1/instances/{self.iid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertGreaterEqual(r.json()["removed"]["tokens"], 1)
        self.assertIsNone(store.get_instance(self.iid))
        # the agent's token went with it, so it can no longer report
        self.assertEqual(bare.post(f"/agent/v1/instances/{self.iid}/heartbeat",
                                   json={"agent_version": "0.1.0", "report": {}}, headers=agent).status_code, 401)
        self.assertEqual(self.admin.get(f"/api/v1/instances/{self.iid}").status_code, 404)
        self.assertEqual(self.admin.delete(f"/api/v1/instances/{self.iid}").status_code, 404)

    def test_history_outlives_the_instance_it_happened_on(self):
        self.admin.delete(f"/api/v1/instances/{self.iid}")
        audit = self.admin.get("/api/v1/audit", params={"limit": 2000}).json()
        actions = [e["action"] for e in audit if e["target"] == self.iid]
        self.assertIn("instance.created", actions)   # the record of it existing survives
        self.assertIn("instance.removed", actions)

    def test_only_an_admin_edits_or_forgets_an_instance(self):
        for role in ("fleet_architect", "operator", "approver", "viewer"):
            client = signed_in(role)
            self.assertEqual(client.patch(f"/api/v1/instances/{self.iid}", json={"environment": "staging"}).status_code, 403, role)
            self.assertEqual(client.delete(f"/api/v1/instances/{self.iid}").status_code, 403, role)
        self.assertIsNotNone(store.get_instance(self.iid))


if __name__ == "__main__":
    unittest.main()
