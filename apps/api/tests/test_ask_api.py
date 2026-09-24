"""Ask the fleet through FastAPI, with the orchestrator's instance played by the test."""

import json
import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
STATE = {"description": "", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:0", "skills": [],
         "toolsets": [], "mcps": []}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class AskApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        tag = os.urandom(3).hex()
        # a zone the orchestrator is granted, one it is not, and one Operators may also read
        self.cases, self.board, self.wide = f"ask-cases-{tag}", f"ask-board-{tag}", f"ask-wide-{tag}"
        for zid, roles in ((self.cases, ["approver"]), (self.board, ["approver"]), (self.wide, ["approver", "operator"])):
            self.assertEqual(self.admin.post("/api/v1/content/zones", json={"id": zid, "name": zid, "read_roles": roles}).status_code, 201)
        self.addCleanup(lambda: [store.delete_zone(z) for z in (self.cases, self.board, self.wide)])
        self.profile = f"orch-{tag}"
        with open(EXAMPLE, encoding="utf-8") as f:
            text = (f.read().replace("name: aml-investigation", f"name: ask-{tag}")
                    .replace("  - id: case-orchestrator\n", f"  - id: case-orchestrator\n    hermes_profile: {self.profile}\n", 1)
                    .replace("content_zones: [case-files]", f"content_zones: [{self.cases}, {self.wide}]", 1))
        self.assertEqual(self.admin.post("/api/v1/blueprints", json={"yaml": text}).status_code, 201)
        store.set_blueprint_status(f"ask-{tag}", 3, "applied")
        self.inst = f"ask-inst-{tag}"
        pair = self.admin.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        job = self.admin.post(f"/api/v1/instances/{self.inst}/import").json()["job_id"]
        self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent)
        self.admin.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "profiles": {self.profile: STATE}}, headers=self.agent)
        self.addCleanup(lambda: store.set_settings({"ask": None}, "tests"))
        self.upload(self.cases, "wire legs.txt", "Two of three wire legs of AML-2026-0412 settle through a Limassol correspondent in Cyprus.", "restricted")
        self.upload(self.board, "board minutes.txt", "The board discussed Cyprus exposure in private session.", "confidential")
        self.upload(self.wide, "kpi.txt", "Cyprus desk throughput was steady this week.", "internal")
        self.approver = signed_in("approver")

    def upload(self, zone, name, text, classification):
        r = self.admin.post("/api/v1/content/files", json={"zone": zone, "name": name, "classification": classification, "text": text})
        self.assertEqual(r.status_code, 201, r.text)

    def configure(self):
        r = self.admin.put("/api/v1/ask/config", json={"instance_id": self.inst, "profile": self.profile})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def answer(self, output, tool_calls=(), **extra):
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], "hermes_run")
        result = {"ok": True, "status": "completed", "output": output, "run_id": "run_1", "session_id": "s1", **extra}
        if tool_calls is not None:
            result["tool_calls"] = list(tool_calls)
        self.assertEqual(self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent).status_code, 200)
        return job

    def test_an_answer_comes_only_from_what_both_the_person_and_the_orchestrator_may_read(self):
        self.assertEqual(self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"}).status_code, 409)
        cfg = self.configure()["config"]
        self.assertEqual(cfg["granted_zones"], sorted([self.cases, self.wide]))
        mine = self.approver.get("/api/v1/ask/config").json()["config"]
        self.assertIn(self.board, mine["not_searched"])  # the approver reads it, the orchestrator is not granted it

        r = self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"})
        self.assertEqual(r.status_code, 201, r.text)
        thread = r.json()
        turn = thread["turns"][0]
        self.assertEqual(turn["status"], "running")
        self.assertEqual({s["zone"] for s in turn["sources"]}, {self.cases, self.wide})
        self.assertNotIn("excerpt", turn["sources"][0])  # what was sent is recorded by reference, not copied again

        job = self.answer(json.dumps({"answer": "AML-2026-0412 settles through Limassol [S1].", "sources": ["S1", "S7"]}))
        self.assertTrue(job["params"]["transcript"])
        self.assertIn("Limassol", job["params"]["instructions"])
        self.assertNotIn("private session", job["params"]["instructions"])  # the board zone was never sent
        turn = self.approver.get(f"/api/v1/ask/threads/{thread['id']}").json()["turns"][0]
        self.assertEqual((turn["status"], turn["cited"], turn["dropped"]), ("answered", ["S1"], ["S7"]))
        self.assertEqual(turn["grounding"]["verdict"], "Evidence found")

        # a conversation is its owner's alone
        self.assertEqual(signed_in("approver").get(f"/api/v1/ask/threads/{thread['id']}").status_code, 404)
        self.assertEqual(self.admin.get(f"/api/v1/ask/threads/{thread['id']}").status_code, 404)
        self.assertEqual(signed_in("viewer").get("/api/v1/ask/threads").status_code, 403)
        self.assertEqual(signed_in("approver").put("/api/v1/ask/config", json={"instance_id": self.inst, "profile": self.profile}).status_code, 403)

    def test_tools_or_a_missing_transcript_are_not_passed_off_as_grounded(self):
        self.configure()
        t = self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"}).json()
        self.answer("Limassol [S1].", tool_calls=[{"name": "web_search", "arguments": "{}"}])
        turn = self.approver.get(f"/api/v1/ask/threads/{t['id']}").json()["turns"][0]
        self.assertEqual((turn["format"], turn["grounding"]["verdict"]), ("text", "Not verifiable"))
        self.assertIn("web_search", turn["grounding"]["detail"])
        self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns", json={"question": "And the wire legs?"})
        self.answer("Two of three [S1].", tool_calls=None)  # an agent without transcripts
        turn = self.approver.get(f"/api/v1/ask/threads/{t['id']}").json()["turns"][1]
        self.assertEqual(turn["grounding"]["verdict"], "Not verifiable")

    def test_a_follow_up_keeps_its_sources_and_waits_for_the_last_answer(self):
        self.configure()
        t = self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"}).json()
        self.assertEqual(self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns", json={"question": "More?"}).status_code, 409)
        self.answer(json.dumps({"answer": "AML-2026-0412 [S1].", "sources": ["S1"]}))
        cited = self.approver.get(f"/api/v1/ask/threads/{t['id']}").json()["turns"][0]["cited_keys"]
        r = self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns", json={"question": "Draft a summary I can paste into the weekly report"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["turns"][1]["sources"][0]["key"], cited[0])
        job = self.answer(json.dumps({"answer": "Summary [S1].", "sources": ["S1"]}))
        self.assertIn("Earlier question: Which cases touch Cyprus?", job["params"]["input"])
        self.assertEqual([row["turns"] for row in self.approver.get("/api/v1/ask/threads").json() if row["id"] == t["id"]], [2])

    def test_a_question_can_be_stopped_and_a_late_answer_is_ignored(self):
        self.configure()
        t = self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"}).json()
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        stopped = self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns/1/stop")
        self.assertEqual(stopped.status_code, 200, stopped.text)
        self.assertEqual(stopped.json()["turns"][0]["status"], "failed")
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json={"ok": True, "output": "late [S1]"}, headers=self.agent)
        self.assertEqual(self.approver.get(f"/api/v1/ask/threads/{t['id']}").json()["turns"][0]["answer"], None)
        self.assertEqual(self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns/1/stop").status_code, 409)

    def test_saving_never_widens_who_can_read_it(self):
        self.configure()
        t = self.approver.post("/api/v1/ask/threads", json={"question": "Which cases touch Cyprus?"}).json()
        sources = t["turns"][0]["sources"]
        s_cases = next(s["id"] for s in sources if s["zone"] == self.cases)
        self.answer(json.dumps({"answer": f"Limassol [{s_cases}].", "sources": [s_cases]}))
        targets = self.approver.get(f"/api/v1/ask/threads/{t['id']}/turns/1/save-targets").json()
        self.assertIn(self.cases, targets["zones"])
        self.assertNotIn(self.wide, targets["zones"])  # Operators read that zone, not the case files
        self.assertEqual(targets["classification"], "restricted")
        bad = self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns/1/save", json={"zone": self.wide, "name": "Cyprus cases"})
        self.assertEqual(bad.status_code, 422)
        r = self.approver.post(f"/api/v1/ask/threads/{t['id']}/turns/1/save", json={"zone": self.cases, "name": "Cyprus cases"})
        self.assertEqual(r.status_code, 201, r.text)
        out = r.json()
        self.assertEqual((out["classification"], out["produced_by"], out["source"]["kind"]), ("restricted", self.profile, "ask"))
        self.assertEqual(self.approver.get(f"/api/v1/ask/threads/{t['id']}").json()["turns"][0]["saved_output"], out["id"])

    def test_an_admin_makes_the_orchestrator_blueprint_for_chosen_zones(self):
        r = self.admin.post("/api/v1/ask/blueprint-asset", json={"instance_id": self.inst, "zones": [self.cases]})
        self.assertEqual(r.status_code, 201, r.text)
        bp = self.admin.get(f"/api/v1/blueprints/fleet-control-orchestrator/{r.json()['version']}").json()["parsed"]
        agent = bp["agents"][0]
        self.assertEqual((agent["id"], agent["toolsets"], agent["content_zones"]), ("fc-orchestrator", [], [self.cases]))
        self.assertEqual(self.admin.post("/api/v1/ask/blueprint-asset", json={"instance_id": self.inst, "zones": ["nope"]}).status_code, 422)
        self.assertEqual(self.approver.post("/api/v1/ask/blueprint-asset", json={"instance_id": self.inst, "zones": [self.cases]}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
