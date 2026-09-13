"""Drift endpoints end to end through FastAPI. Skipped where FastAPI/httpx are not installed
(the stdlib-only test job); the API CI job and a dev install run them."""

import os
import time
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
SKILLS = {"field": "skills", "blueprint": ["list-versioning", "name-matching"], "live": ["list-versioning", "name-matching", "quick-lookup"]}
MODEL = {"field": "model", "blueprint": {"provider": "local", "name": "llama-4-70b-q4"}, "live": {"provider": "local", "name": "llama-4-8b"}}
DRIFT = {"sanctions-screener": [SKILLS], "ownership-tracer": [MODEL]}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class DriftApiTest(unittest.TestCase):
    def setUp(self):
        self.c = TestClient(app)
        self.inst = "drift-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "staging", "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {"surfaces": {"api": "ok"}}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        with open(EXAMPLE, encoding="utf-8") as f:
            self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": f.read()}).status_code, 201)

    def _scan(self, drift=DRIFT):
        job = self.c.post(f"/api/v1/instances/{self.inst}/drift-scan", params={"blueprint": "aml-investigation", "version": 3}).json()["job_id"]
        self.assertEqual(self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()["id"], job)
        r = self.c.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "drift": drift, "scanned": list(drift)}, headers=self.agent)
        self.assertEqual(r.status_code, 200, r.text)

    def _resolve(self, **body):
        return self.c.post(f"/api/v1/instances/{self.inst}/drift/resolve", json=body)

    def _open(self):
        return self.c.get(f"/api/v1/instances/{self.inst}/drift").json()["drift"]

    def test_nothing_to_resolve(self):
        self.assertEqual(self._resolve(action="ignore_once").status_code, 409)

    def test_ignore_once_reports_again(self):
        self._scan()
        self.assertEqual(self._resolve(action="ignore_once").status_code, 200)
        self.assertEqual(self._open(), {})
        self._scan()
        self.assertEqual(self._open(), DRIFT)

    def test_exception_with_expiry(self):
        self._scan()
        field = [{"profile": "sanctions-screener", "field": "skills"}]
        self.assertEqual(self._resolve(action="exception", fields=field).status_code, 422)  # no expiry
        self.assertEqual(self._resolve(action="exception", fields=field, expires_at=time.time() - 5).status_code, 422)
        r = self._resolve(action="exception", fields=field, expires_at=time.time() + 3600, reason="vendor hotfix")
        self.assertEqual(r.status_code, 200, r.text)
        self._scan()
        report = self.c.get(f"/api/v1/instances/{self.inst}/drift").json()
        self.assertEqual(report["drift"], {"ownership-tracer": [MODEL]})
        self.assertEqual(report["exceptions"][0]["field"], "skills")

    def test_revert_creates_plan(self):
        self._scan()
        r = self._resolve(action="revert")
        self.assertEqual(r.status_code, 200, r.text)
        plan = r.json()["plan"]
        self.assertEqual({c["object"] for c in plan["changes"] if c["kind"] == "update"},
                         {"profile sanctions-screener", "profile ownership-tracer"})
        self.assertEqual(self.c.get(f"/api/v1/plans/{plan['id']}").status_code, 200)

    def test_accept_creates_draft_version(self):
        self._scan()
        r = self._resolve(action="accept", fields=[{"profile": "ownership-tracer", "field": "model"}])
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertGreater(out["version"], 3)
        self.assertEqual(out["status"], "draft")
        saved = self.c.get(f"/api/v1/blueprints/aml-investigation/{out['version']}").json()["yaml"]
        self.assertIn("llama-4-8b", saved)
        self.assertEqual(self._open(), {"sanctions-screener": [SKILLS]})

    def test_unknown_field_is_rejected(self):
        self._scan()
        self.assertEqual(self._resolve(action="revert", fields=[{"profile": "challenger", "field": "skills"}]).status_code, 422)

    def test_resolutions_are_audited(self):
        self._scan()
        self._resolve(action="ignore_once")
        actions = [e["action"] for e in self.c.get("/api/v1/audit").json() if self.inst in e["target"]]
        self.assertIn("drift.ignored_once", actions)
        self.assertIn("drift.scan_requested", actions)


if __name__ == "__main__":
    unittest.main()
