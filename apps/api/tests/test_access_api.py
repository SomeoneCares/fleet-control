"""The Access inspector through FastAPI: Admins ask, answers come from the rules the API enforces."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class AccessApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        tag = os.urandom(3).hex()
        self.zone = f"acc-zone-{tag}"
        self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": self.zone, "read_roles": ["approver"]})
        self.addCleanup(lambda: store.delete_zone(self.zone))
        self.bp = f"acc-{tag}"
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: {self.bp}").replace(
                "content_zones: [case-files]", f"content_zones: [{self.zone}]", 1)  # case-orchestrator reads the zone
        self.admin.post("/api/v1/blueprints", json={"yaml": text})
        store.set_blueprint_status(self.bp, 3, "applied")
        self.dana = user("approver")

    def test_a_person_an_agent_and_what_they_reach_together(self):
        r = self.admin.get("/api/v1/access/inspect", params={"email": self.dana, "blueprint": self.bp, "agent": "case-orchestrator"})
        self.assertEqual(r.status_code, 200, r.text)
        doc = r.json()
        perms = {p["permission"]: p["allowed"] for p in doc["person"]["permissions"]}
        self.assertTrue(perms["rooms.decide"])
        self.assertFalse(perms["instances.connect"])
        self.assertIn(self.zone, doc["agent"]["content_zones"])
        together = {z["zone"]: z for z in doc["together"]["zones"]}
        self.assertTrue(together[self.zone]["allowed"])

    def test_the_one_question_answers(self):
        q = {"email": self.dana, "blueprint": self.bp, "agent": "sanctions-screener", "tool": "web.fetch", "environment": "production"}
        checks = self.admin.get("/api/v1/access/inspect", params=q).json()["checks"]
        tool = next(c for c in checks if "web.fetch" in c["question"])
        self.assertEqual((tool["allowed"], tool["layer"]), (False, "policy"))
        self.assertFalse(next(c for c in checks if "production" in c["question"])["allowed"])

        room = self.admin.post("/api/v1/rooms", json={"question": "Should we file a SAR here?", "zone": self.zone,
                                                     "options": ["File", "Wait"]}).json()
        check = self.admin.get("/api/v1/access/inspect", params={"email": self.dana, "room": room["id"]}).json()["checks"][0]
        self.assertEqual((check["allowed"], check["why"]), (True, "they may decide now"))
        subjects = self.admin.get("/api/v1/access/subjects").json()
        self.assertIn(room["id"], [x["id"] for x in subjects["rooms"]])
        self.assertIn("web.fetch", subjects["tools"])
        self.assertIn({"blueprint": self.bp, "version": 3, "agent": "case-orchestrator", "profile": "case-orchestrator"}, subjects["agents"])

    def test_questions_that_need_their_subject_and_who_may_ask(self):
        self.assertEqual(self.admin.get("/api/v1/access/inspect", params={"tool": "web.fetch"}).status_code, 422)
        self.assertEqual(self.admin.get("/api/v1/access/inspect", params={"room": "x"}).status_code, 422)
        self.assertEqual(self.admin.get("/api/v1/access/inspect", params={"email": "nobody@x"}).status_code, 404)
        self.assertEqual(self.admin.get("/api/v1/access/inspect", params={"agent": "ghost"}).status_code, 404)
        self.assertEqual(signed_in("operator").get("/api/v1/access/inspect", params={"email": self.dana}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
