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
# every profile of the example's workflow, with the MCP servers its agents need: a workflow test can rehearse there
WHOLE_FLEET = {p: {**STATE, "mcps": m} for p, m in {
    "case-orchestrator": ["case-store"], "sanctions-screener": ["opensanctions"], "ownership-tracer": ["corporate-registry"],
    "challenger": [], "sar-drafter": ["document-store"]}.items()}
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

    def _start(self, test_id="sanctions-evidence"):
        """Start one run and leave it unanswered, as a hung or slow agent would."""
        r = self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": self.staging,
                                                      "test_ids": [test_id]})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["runs"][0]

    def test_runs_started_within_one_clock_tick_still_have_an_order(self):
        from unittest import mock
        with mock.patch("fleetcontrol_api.main.time.time", return_value=1_000.0):  # a frozen clock: the worst tick
            first, second = self._start(), self._start()
        runs = {r["id"]: r for r in store.list_test_runs(blueprint=self.bp, test_id="sanctions-evidence")}
        self.assertLess(runs[first]["created_at"], runs[second]["created_at"])
        self.assertEqual(store.list_test_runs(blueprint=self.bp, test_id="sanctions-evidence", limit=1)[0]["id"], second)

    def test_a_running_run_is_stopped_and_a_late_answer_cannot_revive_it(self):
        run_id = self._start()
        job = self.c.get(f"/agent/v1/instances/{self.staging}/jobs/next", headers=self.agent).json()  # the agent claims it
        r = self.c.post(f"/api/v1/testlab/runs/{run_id}/cancel")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "cancelled")
        self.assertIn("cancelled by", r.json()["error"])
        # the agent finishes anyway and reports a passing result: the run must stay cancelled
        self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=evidence_for(job["params"]), headers=self.agent)
        after = self.c.get(f"/api/v1/testlab/runs/{run_id}").json()
        self.assertEqual(after["status"], "cancelled")
        self.assertEqual(after["claims"], [])  # no assurance claims from a run nobody wanted
        audit = self.c.get("/api/v1/audit", params={"limit": 2000}).json()
        self.assertTrue(any(e["action"] == "test.cancelled" and run_id in (e.get("detail") or "") for e in audit))

    def test_a_queued_run_is_stopped_before_any_agent_picks_it_up(self):
        run_id = self._start()
        self.assertEqual(self.c.post(f"/api/v1/testlab/runs/{run_id}/cancel").status_code, 200)
        nxt = self.c.get(f"/agent/v1/instances/{self.staging}/jobs/next", headers=self.agent).json()
        self.assertFalse(nxt and nxt.get("meta", {}).get("test_run") == run_id)  # its job was closed

    def test_a_cancelled_run_does_not_become_the_tests_result(self):
        self._run(["sanctions-evidence"])  # a real pass first
        self.c.post(f"/api/v1/testlab/runs/{self._start()}/cancel")
        suites = self.c.get("/api/v1/testlab/suites").json()
        mine = next(s for s in suites if s["blueprint"] == self.bp)
        last = next(t["last_run"] for t in mine["tests"] if t["test"]["id"] == "sanctions-evidence")
        self.assertEqual(last["status"], "passed")  # stopping a run says nothing about the test

    def test_what_cancel_refuses(self):
        done = self._run(["sanctions-evidence"])["runs"][0]
        r = self.c.post(f"/api/v1/testlab/runs/{done}/cancel")
        self.assertEqual(r.status_code, 409)
        self.assertIn("already finished", r.json()["detail"])
        self.assertEqual(self.c.post("/api/v1/testlab/runs/tr_nope/cancel").status_code, 404)
        self.assertEqual(signed_in("viewer").post(f"/api/v1/testlab/runs/{self._start()}/cancel").status_code, 403)

    def test_only_an_admin_deletes_a_run_and_never_one_still_going(self):
        done = self._run(["sanctions-evidence"])["runs"][0]
        for role in ("fleet_architect", "operator", "approver", "viewer"):
            self.assertEqual(signed_in(role).delete(f"/api/v1/testlab/runs/{done}").status_code, 403, role)
        self.assertEqual(self.c.delete(f"/api/v1/testlab/runs/{done}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/v1/testlab/runs/{done}").status_code, 404)
        going = self._start()
        r = self.c.delete(f"/api/v1/testlab/runs/{going}")
        self.assertEqual(r.status_code, 409)
        self.assertIn("cancel it first", r.json()["detail"])
        self.c.post(f"/api/v1/testlab/runs/{going}/cancel")
        self.assertEqual(self.c.delete(f"/api/v1/testlab/runs/{going}").status_code, 200)

    def test_a_suite_runs_on_staging_and_is_judged_from_the_evidence(self):
        out = self._run()
        self.assertEqual(len(out["runs"]), 4)  # the screener's three tests and the challenger's one
        reasons = {s["test_id"]: s["reason"] for s in out["skipped"]}
        self.assertIn("cannot run case-to-sar-draft", reasons["workflow-smoke"])  # staging lacks the orchestrator and others
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
        self.assertEqual((pre["required"], pre["satisfied"], pre["total"], pre["deferred"]), (True, False, 5, []))
        self.assertEqual({t["test_id"]: t["kind"] for t in pre["tests"]}["workflow-smoke"], "workflow")
        r = self.c.post(f"/api/v1/plans/{plan['id']}/apply")
        self.assertEqual(r.status_code, 409)
        self.assertIn("Test Lab", r.json()["detail"])

        self._run(["sanctions-evidence"], answer=lambda p: evidence_for(p, skip=("opensanctions.search",)))  # a failing run
        self._run()  # then the whole suite, passing; the workflow test could not rehearse on this staging
        pre = self.c.get(f"/api/v1/plans/{plan['id']}/preflight").json()
        self.assertEqual((pre["passed"], pre["satisfied"]), (4, False))
        fleet, fleet_agent = self._instance("staging", WHOLE_FLEET)
        self._rehearse(fleet, fleet_agent)
        pre = self.c.get(f"/api/v1/plans/{plan['id']}/preflight").json()
        self.assertEqual((pre["passed"], pre["satisfied"]), (5, True))
        self.assertEqual(self.c.post(f"/api/v1/plans/{plan['id']}/apply").status_code, 200)

    def _rehearse(self, instance, agent, answer=None, test_id="workflow-smoke"):
        """Run a workflow test and play every agent step it queues; returns the test run."""
        r = self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": instance, "test_ids": [test_id]})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["skipped"], [])
        run_id = r.json()["runs"][0]
        answer = answer or (lambda params: {"ok": True, "status": "completed", "output": f"done by {params['profile']}", "run_id": "r1",
                                            "session_id": "s1", "tool_calls": [{"name": "read_file"}], "usage": {"total_tokens": 1000}})
        for _ in range(10):
            if self.c.get(f"/api/v1/testlab/runs/{run_id}").json()["status"] != "running":
                break  # judged: nothing more is queued (and asking would wait out the agents' long poll)
            job = self.c.get(f"/agent/v1/instances/{instance}/jobs/next", headers=agent).json()
            self.assertEqual(job["kind"], "hermes_run", job)
            self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=answer(job["params"]), headers=agent)
        return self.c.get(f"/api/v1/testlab/runs/{run_id}").json()

    def test_a_workflow_test_rehearses_the_workflow_and_is_judged_on_what_the_run_produced(self):
        fleet, agent = self._instance("staging", WHOLE_FLEET)
        rooms_before = {r["id"] for r in self.c.get("/api/v1/rooms").json()}
        run = self._rehearse(fleet, agent)
        self.assertEqual(run["status"], "passed", run["checks"])
        self.assertEqual({c["id"]: c["outcome"] for c in run["checks"]}, {"artifact": "pass", "limit:time": "pass", "limit:tokens": "pass"})
        self.assertEqual(run["usage"], {"total_tokens": 5000})  # five agent runs
        self.assertEqual({c["agent"] for c in run["tool_calls"]}, {"case-orchestrator", "sanctions-screener", "ownership-tracer",
                                                                   "challenger", "sar-drafter"})
        wf = self.c.get(f"/api/v1/workflows/runs/{run['workflow_run']}").json()
        self.assertEqual((wf["status"], wf["test_run"], wf["room_id"]), ("done", run["id"], None))
        gate = wf["steps"][3]["gate"]
        self.assertEqual((gate["approved"], gate["automatic"], gate["by"]), (True, True, "fleetcontrol"))
        self.assertEqual({r["id"] for r in self.c.get("/api/v1/rooms").json()}, rooms_before)  # nobody is asked to decide a test
        self.assertEqual([o for o in self.c.get("/api/v1/outputs").json() if (o.get("source") or {}).get("workflow_run") == wf["id"]], [])

    def test_a_workflow_test_fails_when_a_step_fails_or_the_artifact_is_missing(self):
        fleet, agent = self._instance("staging", WHOLE_FLEET)
        broken = self._rehearse(fleet, agent, answer=lambda p: {"ok": p["profile"] != "challenger", "status": "completed",
                                                                "output": "x" if p["profile"] != "challenger" else None,
                                                                "error": "provider down", "tool_calls": []})
        self.assertEqual(broken["status"], "error")
        self.assertIn("challenger did not finish", broken["checks"][0]["detail"])
        blind = self._rehearse(fleet, agent, answer=lambda p: {"ok": True, "status": "completed", "output": "x", "run_id": "r"})
        self.assertEqual(blind["tool_calls"], None)  # no transcript on any step: tool checks would be Not verifiable, never guessed

    def test_stopping_a_workflow_test_stops_its_workflow(self):
        fleet, agent = self._instance("staging", WHOLE_FLEET)
        r = self.c.post("/api/v1/testlab/runs", json={"blueprint": self.bp, "version": 3, "instance_id": fleet, "test_ids": ["workflow-smoke"]})
        run_id = r.json()["runs"][0]
        wf_id = self.c.get(f"/api/v1/testlab/runs/{run_id}").json()["workflow_run"]
        self.assertEqual(self.c.post(f"/api/v1/testlab/runs/{run_id}/cancel").json()["status"], "cancelled")
        self.assertEqual(self.c.get(f"/api/v1/workflows/runs/{wf_id}").json()["status"], "cancelled")
        wf = self.c.get(f"/api/v1/workflows/runs/{wf_id}").json()
        job = store.get_job(wf["steps"][0]["jobs"]["case-orchestrator"])
        self.assertEqual(job["status"], "failed")  # its step job was closed, so the agent never picks it up

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
