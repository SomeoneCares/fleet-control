"""Test Lab, Assurance and the production test gate through FastAPI, with the agent played by the test.
Each test uses its own copy of the example blueprint, so runs from other tests never count. Skipped without FastAPI."""

import csv
import io
import json
import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from fleetcontrol_api.settings import DEFAULTS
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
STATE = {"description": "", "model": {"provider": "local", "name": "llama-4-70b-q4"}, "soul_sha256": "sha256:0", "soul_text": "",
         "skills": [], "toolsets": [], "mcps": []}
GOOD_OUTPUT = json.dumps({"match_count": 0, "matches": [], "list_versions": {"OFAC": "2026-09-15"}, "screened_at": "2026-09-16",
                          "note": "That claim is unsupported."})


def evidence_for(params, *, skip=()):
    """What a well-behaved agent returns: it calls the required tools, writes the artifact, answers in contract."""
    hints = params.get("hints") or {}
    calls = [{"name": "mcp_" + t.replace(".", "__", 1) if "." in t else t, "arguments": "{}", "result": "ok", "answered": True}
             for t in hints.get("required_tools") or [] if t not in skip]
    if hints.get("expected_artifact"):
        calls.append({"name": "write_file", "arguments": json.dumps({"path": hints["expected_artifact"]}), "result": "ok", "answered": True})
    return {"ok": True, "status": "completed", "output": GOOD_OUTPUT, "usage": {"total_tokens": 900}, "duration_s": 5.0,
            "tool_calls": calls, "evidence": "transcript", "session_id": "s1", "run_id": "r1"}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class TestLabApiTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
        self.bp = "aml-" + os.urandom(3).hex()
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: {self.bp}")
        self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": text}).status_code, 201)
        self.staging, self.agent = self._instance("staging", {"sanctions-screener": STATE, "challenger": STATE})

    def tearDown(self):
        store.set_settings({"require_tests_for_production": DEFAULTS["require_tests_for_production"]}, "tests")

    def _instance(self, environment, profiles):
        iid = f"tl-{environment[:4]}-{os.urandom(3).hex()}"
        pair = self.c.post("/api/v1/instances", json={"id": iid, "environment": environment, "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": iid, "agent_version": "0.1.0", "report": {}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        agent = {"Authorization": f"Bearer {tok}"}
        job = self.c.post(f"/api/v1/instances/{iid}/import").json()["job_id"]
        self.c.get(f"/agent/v1/instances/{iid}/jobs/next", headers=agent)
        self.c.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "profiles": profiles}, headers=agent)
        return iid, agent

    def _run(self, test_ids=None, answer=evidence_for, instance=None, agent=None):
        instance, agent = instance or self.staging, agent or self.agent
        r = self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": instance, "test_ids": test_ids})
        self.assertEqual(r.status_code, 201, r.text)
        for _ in r.json()["runs"]:
            job = self.c.get(f"/agent/v1/instances/{instance}/jobs/next", headers=agent).json()
            self.assertEqual(job["kind"], "run_test")
            self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=answer(job["params"]), headers=agent)
        return r.json()

    def test_a_suite_runs_on_staging_and_is_judged_from_the_evidence(self):
        out = self._run()
        self.assertEqual(len(out["runs"]), 4)  # the screener's three tests and the challenger's one
        reasons = {s["test_id"]: s["reason"] for s in out["skipped"]}
        self.assertIn("Workflows", reasons["workflow-smoke"])
        runs = {r["test_id"]: r for r in self.c.get("/api/v1/testlab/runs", params={"blueprint": self.bp}).json()}
        self.assertEqual({t: r["status"] for t, r in runs.items()},
                         {"sanctions-evidence": "passed", "alias-match": "passed", "no-web-fetch": "passed", "objects-to-unsupported-claim": "passed"})
        detail = self.c.get(f"/api/v1/testlab/runs/{runs['sanctions-evidence']['id']}").json()
        self.assertEqual(detail["tool_calls"][0]["name"], "mcp_opensanctions__search")
        suite = next(s for s in self.c.get("/api/v1/testlab/suites").json() if s["blueprint"] == self.bp)
        self.assertTrue(suite["gates_production"])
        self.assertEqual({t["test"]["id"]: (t["last_run"] or {}).get("status") for t in suite["tests"]}["workflow-smoke"], None)

        claims = [c for c in self.c.get("/api/v1/assurance/claims").json() if c["blueprint"] == self.bp]
        self.assertIn(("Called opensanctions.search", "Evidence found"), {(c["claim"], c["verdict"]) for c in claims})
        summary = self.c.get("/api/v1/assurance/summary").json()
        self.assertGreaterEqual(summary["counts"]["Evidence found"], 1)
        rows = list(csv.reader(io.StringIO(self.c.get("/api/v1/assurance/export").text)))
        self.assertEqual(rows[0][:3], ["time_utc", "instance", "blueprint"])
        self.assertTrue(any(r[2] == self.bp and r[7] == "Evidence found" for r in rows[1:]))

    def test_a_screening_that_did_not_run_is_caught(self):
        self._run(["sanctions-evidence"], answer=lambda p: evidence_for(p, skip=("opensanctions.search",)))
        run = self.c.get("/api/v1/testlab/runs", params={"blueprint": self.bp, "test_id": "sanctions-evidence"}).json()[0]
        self.assertEqual(run["status"], "failed")
        claims = [c for c in self.c.get("/api/v1/assurance/claims", params={"verdict": "No evidence"}).json() if c["run_id"] == run["id"]]
        self.assertEqual([c["claim"] for c in claims], ["Called opensanctions.search"])
        self.assertEqual(self.c.get("/api/v1/assurance/claims", params={"verdict": "Maybe"}).status_code, 422)

    def test_where_tests_may_run(self):
        prod, _ = self._instance("production", {"sanctions-screener": STATE})
        self.assertEqual(self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": prod}).status_code, 409)
        lab, lab_agent = self._instance("lab", {"challenger": STATE})
        out = self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": lab,
                                                         "test_ids": ["sanctions-evidence", "objects-to-unsupported-claim"]}).json()
        self.assertEqual(len(out["runs"]), 1)
        self.assertIn("apply the blueprint there", out["skipped"][0]["reason"])
        self.c.get(f"/agent/v1/instances/{lab}/jobs/next", headers=lab_agent)  # take the queued job
        self.assertEqual(self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": lab,
                                                                   "test_ids": ["nope"]}).status_code, 404)
        self.assertEqual(signed_in("viewer").post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3,
                                                                                "instance_id": lab}).status_code, 403)
        self.assertEqual(signed_in("approver").get("/api/v1/assurance/claims").status_code, 403)

    def test_production_waits_for_a_passing_suite(self):
        prod, prod_agent = self._instance("production", {})
        plan = self.c.post("/api/v1/plans", json={"blueprint": self.bp, "version": 3, "instance_id": prod}).json()
        for _ in range(2):
            self.assertEqual(signed_in("approver").post(f"/api/v1/plans/{plan['id']}/approve").status_code, 200)
        pre = self.c.get(f"/api/v1/plans/{plan['id']}/preflight").json()
        self.assertEqual((pre["required"], pre["satisfied"], pre["total"], pre["deferred"]), (True, False, 4, ["workflow-smoke"]))
        r = self.c.post(f"/api/v1/plans/{plan['id']}/apply")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Test Lab", r.json()["detail"])

        self._run(["sanctions-evidence"], answer=lambda p: evidence_for(p, skip=("opensanctions.search",)))  # a failing run
        self._run()  # then the whole suite, passing
        pre = self.c.get(f"/api/v1/plans/{plan['id']}/preflight").json()
        self.assertEqual((pre["passed"], pre["satisfied"]), (4, True))
        self.assertEqual(self.c.post(f"/api/v1/plans/{plan['id']}/apply").status_code, 200)

    def test_the_gate_can_be_turned_off(self):
        prod, _ = self._instance("production", {})
        store.set_settings({"require_tests_for_production": False}, "tests")
        plan = self.c.post("/api/v1/plans", json={"blueprint": self.bp, "version": 3, "instance_id": prod}).json()
        self.assertEqual(self.c.get(f"/api/v1/plans/{plan['id']}/preflight").json()["satisfied"], True)
        staging_plan = self.c.post("/api/v1/plans", json={"blueprint": self.bp, "version": 3, "instance_id": self.staging}).json()
        self.assertEqual(self.c.get(f"/api/v1/plans/{staging_plan['id']}/preflight").json()["required"], False)

    def test_tests_are_edited_on_drafts(self):
        base = f"/api/v1/blueprints/{self.bp}/3/tests"
        new = {"target": "sanctions-screener", "scenario": "Screen Acme Shipping and cite the list versions.",
               "required_tools": ["opensanctions.search"], "evaluator": "contains", "expected": "list_versions"}
        r = self.c.put(f"{base}/cites-list-versions", json=new)
        self.assertEqual((r.status_code, r.json()["created"]), (200, True), r.text)
        agents = {a["id"]: a for a in self.c.get(f"/api/v1/blueprints/{self.bp}/3").json()["parsed"]["agents"]}
        self.assertIn("cites-list-versions", agents["sanctions-screener"]["tests"])
        self.assertEqual(self.c.put(f"{base}/cites-list-versions", json={"expected": "list_versions:"}).json()["created"], False)
        self.assertEqual(self.c.put(f"{base}/orphan", json={**new, "target": "nobody"}).status_code, 422)
        self.assertEqual(self.c.put(f"{base}/Bad Id", json=new).status_code, 422)
        self.assertEqual(self.c.delete(f"{base}/cites-list-versions").status_code, 200)
        self.assertEqual(self.c.delete(f"{base}/cites-list-versions").status_code, 404)
        store.set_blueprint_status(self.bp, 3, "applied")
        self.assertEqual(self.c.put(f"{base}/late", json=new).status_code, 409)


if __name__ == "__main__":
    unittest.main()
