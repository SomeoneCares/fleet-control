"""Workflow runs through FastAPI, with the instance's agent played by the test."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
PROFILES = {"case-orchestrator": ["case-store"], "sanctions-screener": ["opensanctions"], "ownership-tracer": ["corporate-registry"],
            "challenger": [], "sar-drafter": ["document-store"]}


def state(mcps):
    return {"description": "", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:0", "skills": [],
            "toolsets": [], "mcps": mcps}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class WorkflowsApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        tag = self.tag = os.urandom(3).hex()
        self.bp = f"wf-{tag}"
        with open(EXAMPLE, encoding="utf-8") as f:
            self.assertEqual(self.admin.post("/api/v1/blueprints", json={"yaml": f.read().replace("name: aml-investigation", f"name: {self.bp}")}).status_code, 201)
        store.set_blueprint_status(self.bp, 3, "applied")
        self.zone = f"wf-zone-{tag}"
        self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": self.zone, "read_roles": ["approver", "operator"]})
        self.addCleanup(lambda: store.delete_zone(self.zone))
        self.inst, self.agent = self.instance(PROFILES)
        self.operator = signed_in("operator")
        self.approver_email = user("approver")
        self.approver = signed_in("approver", self.approver_email)

    def instance(self, profiles):
        iid = f"wfi-{os.urandom(3).hex()}"
        pair = self.admin.post("/api/v1/instances", json={"id": iid, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": iid, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        agent = {"Authorization": f"Bearer {tok}"}
        job = self.admin.post(f"/api/v1/instances/{iid}/import").json()["job_id"]
        self.admin.get(f"/agent/v1/instances/{iid}/jobs/next", headers=agent)
        self.admin.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "profiles": {p: state(m) for p, m in profiles.items()}}, headers=agent)
        return iid, agent

    def answer(self, output="done", ok=True, agent=None):
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=agent or self.agent).json()
        self.assertEqual(job["kind"], "hermes_run", job)
        result = {"ok": ok, "status": "completed" if ok else "failed", "output": output if ok else None, "run_id": "r1",
                  "session_id": "s1", "tool_calls": [], **({} if ok else {"error": "provider down"})}
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=agent or self.agent)
        return job

    def start(self, **extra):
        return self.operator.post("/api/v1/workflows/runs", json={"blueprint": self.bp, "workflow_id": "case-to-sar-draft",
                                                                  "instance_id": self.inst, "input": "Case AML-2026-0412: wires to Limassol",
                                                                  "case": "AML-2026-0412", "zone": self.zone, **extra})

    def test_a_run_goes_from_intake_through_the_gate_to_a_decision_room(self):
        wf = next(w for w in self.admin.get("/api/v1/workflows").json() if w["blueprint"] == self.bp)
        self.assertEqual((len(wf["steps"]), wf["gates"]), (6, 1))
        self.assertTrue(next(r for r in wf["readiness"] if r["instance_id"] == self.inst)["ready"])

        r = self.start()
        self.assertEqual(r.status_code, 201, r.text)
        run_id = r.json()["id"]
        first = self.answer("Brief: four entities, a Limassol correspondent.")
        self.assertEqual((first["params"]["profile"], first["params"]["transcript"]), ("case-orchestrator", True))
        para = sorted(self.answer(o)["params"]["profile"] for o in ("0 matches", "UBO: J. Doe"))
        self.assertEqual(para, ["ownership-tracer", "sanctions-screener"])  # both queued at once
        challenger = self.answer("Memo v1")
        self.assertIn("UBO: J. Doe", challenger["params"]["input"])  # it reviews what the others produced

        mine = self.approver.get("/api/v1/workflows/waiting-for-me").json()
        self.assertEqual([(m["id"], m["gate"]["index"]) for m in mine if m["id"] == run_id], [(run_id, 3)])
        self.assertEqual([m for m in self.operator.get("/api/v1/workflows/waiting-for-me").json() if m["id"] == run_id], [])
        run = self.approver.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual((run["status"], run["row"]["awaiting_role"], run["may_decide"]["3"]["allowed"]), ("waiting", "approver", True))
        self.assertEqual(self.operator.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": True}).status_code, 409)
        self.assertEqual(self.approver.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": False}).status_code, 422)
        back = self.approver.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": False, "note": "Check the second wire leg"})
        self.assertEqual(back.status_code, 200, back.text)
        again = self.answer("Memo v2")
        self.assertEqual(again["params"]["profile"], "challenger")
        self.assertIn("Check the second wire leg", again["params"]["input"])
        self.assertEqual(self.approver.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": True}).status_code, 200)
        self.assertEqual(self.answer("SAR draft text")["params"]["profile"], "sar-drafter")

        run = self.admin.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual(run["status"], "done")
        room = self.admin.get(f"/api/v1/rooms/{run['room_id']}").json()
        self.assertEqual((room["case"], room["zone"]), ("AML-2026-0412", self.zone))
        self.assertIn("sar-draft.docx (by sar-drafter)", [e["label"] for e in room["evidence"]])
        out_id = run["artifacts"]["challenge-memo.md"]["artifact_id"]
        self.assertEqual(self.admin.get(f"/api/v1/outputs/{out_id}").json()["text"], "Memo v2")  # the latest attempt
        actions = [e["action"] for e in self.admin.get("/api/v1/audit").json() if e["target"] == run_id]
        for a in ("workflow.started", "workflow.gate_sent_back", "workflow.gate_approved", "workflow.room_opened"):
            self.assertIn(a, actions)

    def test_an_overdue_gate_escalates_on_the_next_heartbeat(self):
        run_id = self.start().json()["id"]
        for out in ("brief", "0 matches", "UBO", "memo"):
            self.answer(out)
        def backdate(d):
            d["steps"][3]["started_at"] -= 25 * 3600
        store.update_workflow_run(run_id, backdate)
        self.admin.post(f"/agent/v1/instances/{self.inst}/heartbeat", json={"agent_version": "0.1.0", "report": {}}, headers=self.agent)
        step = store.get_workflow_run(run_id)["steps"][3]
        self.assertIsNotNone(step["escalated_at"])
        self.assertTrue(any(e["action"] == "workflow.gate_escalated" and "no role or account 'mlro'" in e["detail"]
                            for e in self.admin.get("/api/v1/audit").json() if e["target"] == run_id))

    def test_what_is_refused_and_a_run_can_be_stopped(self):
        self.assertEqual(self.start(zone=None).status_code, 422)  # the room needs a zone
        self.assertEqual(self.approver.post("/api/v1/workflows/runs", json={}).status_code, 403)
        half, _ = self.instance({"case-orchestrator": ["case-store"]})
        r = self.start(instance_id=half)
        self.assertEqual(r.status_code, 409)
        self.assertIn("sanctions-screener runs as profile sanctions-screener", r.text)

        run_id = self.start().json()["id"]
        stopped = self.operator.post(f"/api/v1/workflows/runs/{run_id}/cancel")
        self.assertEqual(stopped.json()["status"], "cancelled")
        self.assertEqual(self.operator.post(f"/api/v1/workflows/runs/{run_id}/cancel").status_code, 409)

    def test_a_failed_step_fails_the_run(self):
        run_id = self.start().json()["id"]
        self.answer(ok=False)
        run = self.admin.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual((run["status"], run["error"]), ("failed", "case-orchestrator did not finish"))


    def kanban_on(self):
        self.admin.post(f"/agent/v1/instances/{self.inst}/heartbeat", headers=self.agent,
                        json={"agent_version": "0.1.0", "report": {"kanban": {"available": True, "dispatching": True, "why": None}}})

    def board(self, tasks_state):
        """Play the agent's Kanban: answer a kanban_read with these states, keyed by agent."""
        self.admin.post(f"/agent/v1/instances/{self.inst}/heartbeat", headers=self.agent, json={"agent_version": "0.1.0",
                        "report": {"kanban": {"available": True, "dispatching": True, "why": None}}})
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], "kanban_read", job)
        ids = job["params"]["task_ids"]
        result = {"ok": True, "tasks": {t: tasks_state.get(self.task_agent[t], {"status": "running"}) for t in ids}}
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent)

    def submitted(self):
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], "kanban_submit", job)
        ids = {t["key"]: f"t_{os.urandom(3).hex()}" for t in job["params"]["tasks"]}
        self.task_agent = {**getattr(self, "task_agent", {}), **{ids[t["key"]]: t["agent"] for t in job["params"]["tasks"]}}
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json={"ok": True, "ids": ids}, headers=self.agent)
        return job

    def test_on_kanban_a_run_is_linked_tasks_and_the_gate_stays_in_the_portal(self):
        self.kanban_on()
        r = self.start()
        self.assertEqual(r.status_code, 201, r.text)
        run_id = r.json()["id"]
        self.assertEqual(r.json()["executor"], "kanban")
        job = self.submitted()
        tasks = job["params"]["tasks"]
        self.assertEqual([t["assignee"] for t in tasks], ["case-orchestrator", "sanctions-screener", "ownership-tracer", "challenger"])
        self.assertEqual((job["params"]["board"], tasks[0]["tenant"]), ("fleetcontrol", run_id))
        self.assertIn("Case AML-2026-0412", tasks[0]["body"])

        self.board({"case-orchestrator": {"status": "done", "summary": "Brief"}})
        self.assertEqual(store.get_workflow_run(run_id)["steps"][0]["status"], "done")
        self.board({"sanctions-screener": {"status": "done", "summary": "0 matches"}, "ownership-tracer": {"status": "done", "summary": "UBO"},
                    "challenger": {"status": "done", "summary": "Memo v1"}})
        run = self.approver.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual((run["status"], run["row"]["awaiting_role"]), ("waiting", "approver"))  # Kanban never passes a gate
        self.approver.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": False, "note": "Check the second wire leg"})
        again = self.submitted()["params"]["tasks"]
        self.assertEqual([(t["agent"], t["key"]) for t in again], [("challenger", "2:challenger:1")])  # a fresh task, not the old one
        self.assertIn("Check the second wire leg", again[0]["body"])
        self.assertIn("Memo v1", again[0]["body"])  # it is given what came before, since its parents are not on the board
        self.board({"challenger": {"status": "done", "summary": "Memo v2"}})
        self.approver.post(f"/api/v1/workflows/runs/{run_id}/gates/3", json={"approve": True})
        self.assertEqual(self.submitted()["params"]["tasks"][0]["agent"], "sar-drafter")
        self.board({"sar-drafter": {"status": "done", "summary": "SAR draft"}})
        run = self.admin.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual((run["status"], bool(run["room_id"])), ("done", True))
        self.assertTrue(run["steps"][4]["results"]["sar-drafter"]["kanban_task"].startswith("t_"))

    def test_a_blocked_kanban_task_fails_the_run_and_cancel_archives_what_is_open(self):
        self.kanban_on()
        run_id = self.start().json()["id"]
        self.submitted()
        self.board({"case-orchestrator": {"status": "blocked", "error": "profile case-orchestrator crashed twice"}})
        run = self.admin.get(f"/api/v1/workflows/runs/{run_id}").json()
        self.assertEqual(run["status"], "failed")
        self.assertIn("crashed twice", run["steps"][0]["results"]["case-orchestrator"]["error"])

        run_id = self.start().json()["id"]
        self.submitted()
        self.operator.post(f"/api/v1/workflows/runs/{run_id}/cancel")
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual((job["kind"], len(job["params"]["task_ids"])), ("kanban_cancel", 4))

    def test_kanban_is_chosen_only_where_it_dispatches(self):
        self.assertEqual(self.start(executor="kanban").status_code, 409)  # the agent has not reported Kanban
        self.assertEqual(self.start().json()["executor"], "runs")  # auto falls back to Hermes runs

if __name__ == "__main__":
    unittest.main()
