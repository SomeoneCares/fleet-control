"""The list/detail shapes the web client relies on. Skipped without FastAPI/httpx."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class WebEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.inst = "web-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        self.agent = {"Authorization": "Bearer " + self.c.post(
            "/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {"surfaces": {"api": "ok"}}},
            headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]}
        with open(EXAMPLE, encoding="utf-8") as f:
            self.c.post("/api/v1/blueprints", json={"yaml": f.read()})

    def _agent_does(self, result):
        job = self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent)
        return job

    def _row(self):
        return next(i for i in self.c.get("/api/v1/instances").json() if i["id"] == self.inst)

    def test_instance_row_counts_and_applied_version(self):
        self.assertEqual(self._row()["live_profile_count"], 0)
        self.assertIsNone(self._row()["applied"])
        self.c.post(f"/api/v1/instances/{self.inst}/import")
        self._agent_does({"ok": True, "profiles": {}})
        plan = self.c.post("/api/v1/plans", json={"blueprint": "aml-investigation", "version": 3, "instance_id": self.inst}).json()
        self.c.post(f"/api/v1/plans/{plan['id']}/apply")
        self._agent_does({"ok": True, "written": []})       # push_policy
        self._agent_does({"ok": True, "results": []})       # apply
        scan = self._agent_does({"ok": True, "drift": {}, "scanned": []})
        self.assertEqual(scan["kind"], "drift_scan")        # drift detection starts right after an apply
        self.assertEqual(scan["params"]["managed"]["challenger"]["skills"], ["citation-check", "counter-argument", "redaction-check"])
        row = self._row()
        self.assertEqual(row["open_drift"], 0)
        self.assertEqual(row["applied"]["version"], 3)
        self.assertEqual(row["applied"]["plan_id"], plan["id"])
        self.assertEqual(self.c.get(f"/api/v1/plans/{plan['id']}").json()["status"], "applied")
        self.assertNotIn("pairing_token", row)
        bp = next(b for b in self.c.get("/api/v1/blueprints").json() if b["name"] == "aml-investigation")
        self.assertIn(self.inst, [a["instance"] for a in bp["applied_on"]])

    def test_open_drift_count(self):
        self.c.post(f"/api/v1/instances/{self.inst}/drift-scan", params={"blueprint": "aml-investigation", "version": 3})
        self._agent_does({"ok": True, "drift": {"challenger": [{"field": "skills", "blueprint": [], "live": ["x"]}]}})
        self.assertEqual(self._row()["open_drift"], 1)

    def test_blueprint_list_and_history(self):
        bp = next(b for b in self.c.get("/api/v1/blueprints").json() if b["name"] == "aml-investigation")
        self.assertEqual((bp["owner"], bp["agents"], bp["workflows"], bp["tests"]), ("dana.whitfield", 5, 1, 5))
        history = self.c.get("/api/v1/blueprints/aml-investigation").json()
        self.assertEqual(history[0]["version"], max(h["version"] for h in history))
        self.assertEqual(self.c.get("/api/v1/blueprints/schema").json()["title"], "Blueprint")  # not shadowed
        self.assertEqual(self.c.get("/api/v1/blueprints/no-such-blueprint").status_code, 404)


if __name__ == "__main__":
    unittest.main()
