"""The list/detail shapes the web client relies on. Skipped without FastAPI/httpx."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class WebEndpointsTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
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

    def _own_blueprint(self):
        name = "studio-" + os.urandom(3).hex()
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: {name}", 1)
        self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": text}).status_code, 201)
        return name, text

    def test_detail_carries_parsed_and_managed(self):
        name, _ = self._own_blueprint()
        d = self.c.get(f"/api/v1/blueprints/{name}/3").json()
        self.assertEqual(len(d["parsed"]["agents"]), 5)
        self.assertTrue(d["managed"]["challenger"]["soul_sha256"].startswith("sha256:"))

    def test_edit_draft_and_immutable_applied(self):
        from fleetcontrol_api.main import store

        name, text = self._own_blueprint()
        r = self.c.put(f"/api/v1/blueprints/{name}/3/agents/challenger", json={"skills": ["counter-argument", "quick-check"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["changed"], ["skills"])
        self.assertIn("quick-check", self.c.get(f"/api/v1/blueprints/{name}/3").json()["yaml"])
        self.assertEqual(self.c.put(f"/api/v1/blueprints/{name}/3/agents/challenger", json={"delegates_to": ["ghost"]}).status_code, 422)
        self.assertEqual(self.c.put(f"/api/v1/blueprints/{name}/3/agents/ghost", json={"role": "x"}).status_code, 404)

        store.blueprints[name][3]["status"] = "applied"
        self.assertEqual(self.c.put(f"/api/v1/blueprints/{name}/3/agents/challenger", json={"role": "x"}).status_code, 409)
        self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": text}).status_code, 409)  # no overwrite of applied
        draft = self.c.post(f"/api/v1/blueprints/{name}/3/draft").json()
        self.assertEqual((draft["version"], draft["status"]), (4, "draft"))
        self.assertEqual(self.c.put(f"/api/v1/blueprints/{name}/4/agents/challenger", json={"role": "Counter-case."}).status_code, 200)
        actions = [e["action"] for e in self.c.get("/api/v1/audit", params={"limit": 50}).json() if name in e["target"]]
        self.assertIn("blueprint.edited", actions)
        self.assertIn("blueprint.draft_created", actions)

    def test_audit_export_is_csv(self):
        r = self.c.get("/api/v1/audit/export")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/csv"))
        self.assertTrue(r.text.startswith("time_utc,actor,action,target,detail"))

    def test_blueprint_list_and_history(self):
        bp = next(b for b in self.c.get("/api/v1/blueprints").json() if b["name"] == "aml-investigation")
        self.assertEqual((bp["owner"], bp["agents"], bp["workflows"], bp["tests"]), ("dana.whitfield", 5, 1, 5))
        history = self.c.get("/api/v1/blueprints/aml-investigation").json()
        self.assertEqual(history[0]["version"], max(h["version"] for h in history))
        self.assertEqual(self.c.get("/api/v1/blueprints/schema").json()["title"], "Blueprint")  # not shadowed
        self.assertEqual(self.c.get("/api/v1/blueprints/no-such-blueprint").status_code, 404)


if __name__ == "__main__":
    unittest.main()
