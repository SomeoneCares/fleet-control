"""Mission library through FastAPI: list, read, provision a draft. Skipped without FastAPI/httpx."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

MODEL = {"provider": "nous", "name": "upstage/solar-pro4:free"}
LIVE = {"default": {"description": "", "model": MODEL, "soul_sha256": "sha256:0", "soul_text": "x", "skills": [], "toolsets": [], "mcps": []}}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class MissionsApiTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
        self.inst = "mis-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}

    def _import_live(self):
        self.c.post(f"/api/v1/instances/{self.inst}/import")
        job = self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], "import_profiles")
        self.c.post(f"/agent/v1/jobs/{job['id']}/result", json={"ok": True, "profiles": LIVE}, headers=self.agent)

    def test_list_and_read(self):
        out = self.c.get("/api/v1/missions").json()
        self.assertEqual(len(out["missions"]), 24)
        self.assertEqual(len(out["sectors"]), 8)
        m = self.c.get("/api/v1/missions/ecl-movement-explanation").json()
        self.assertEqual(m["sas"]["reach"], "connector")
        self.assertIn("sas-viya-read-only", m["blueprint_yaml"])
        self.assertEqual(self.c.get("/api/v1/missions/nope").status_code, 404)

    def test_provision_needs_the_instance_model(self):
        r = self.c.post("/api/v1/missions/credit-early-warning/blueprint", json={"instance_id": self.inst})
        self.assertEqual(r.status_code, 409, r.text)

    def test_provision_creates_a_draft(self):
        self._import_live()
        name = "cew-" + os.urandom(2).hex()
        r = self.c.post("/api/v1/missions/credit-early-warning/blueprint", json={"instance_id": self.inst, "name": name})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["status"], r.json()["version"]), ("draft", 1))
        bp = self.c.get(f"/api/v1/blueprints/{name}/1").json()["parsed"]
        self.assertEqual({a["model"]["name"] for a in bp["agents"]}, {MODEL["name"]})
        self.assertEqual(bp["targets"][0]["instance"], self.inst)
        self.assertEqual(bp["metadata"]["labels"]["mission"], "credit-early-warning")
        again = self.c.post("/api/v1/missions/credit-early-warning/blueprint", json={"instance_id": self.inst, "name": name})
        self.assertEqual(again.json()["version"], 2)

    def test_connector_missions_say_what_they_need(self):
        self._import_live()
        r = self.c.post("/api/v1/missions/ecl-movement-explanation/blueprint",
                        json={"instance_id": self.inst, "name": "ecl-" + os.urandom(2).hex()})
        self.assertEqual(r.json()["connectors_needed"], ["sas-risk-cirrus"])

    def test_bad_name_is_refused(self):
        self._import_live()
        r = self.c.post("/api/v1/missions/credit-early-warning/blueprint", json={"instance_id": self.inst, "name": "Bad Name"})
        self.assertEqual(r.status_code, 422)

    def test_roles(self):
        viewer = signed_in("viewer")
        self.assertEqual(viewer.get("/api/v1/missions").status_code, 200)
        self.assertEqual(viewer.post("/api/v1/missions/credit-early-warning/blueprint", json={"instance_id": self.inst}).status_code, 403)
        self.assertEqual(signed_in("approver").get("/api/v1/missions").status_code, 403)


if __name__ == "__main__":
    unittest.main()
