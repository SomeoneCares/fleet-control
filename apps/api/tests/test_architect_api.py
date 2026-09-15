"""Fleet Architect end to end through FastAPI, with the agent's side played by the test. Skipped without FastAPI/httpx."""

import json
import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
    from tests.test_architect import PROPOSAL
except ImportError:  # pragma: no cover
    TestClient = None

MISSION = "Triage incoming vendor invoices and prepare a payment batch for a finance approver."
MODEL = {"provider": "nous", "name": "upstage/solar-pro4:free"}
LIVE = {
    "default": {"description": "", "model": MODEL, "soul_sha256": "sha256:0", "soul_text": "x", "skills": ["pdf"], "toolsets": ["web"], "mcps": []},
    "fc-architect": {"description": "", "model": MODEL, "soul_sha256": "sha256:1", "soul_text": "y", "skills": ["docx"], "toolsets": [], "mcps": []},
}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ArchitectApiTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
        self.inst = "arc-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.c.post(f"/api/v1/instances/{self.inst}/import")
        self._agent_answers("import_profiles", {"ok": True, "profiles": LIVE})
        r = self.c.put("/api/v1/architect/config", json={"instance_id": self.inst, "profile": "fc-architect"})
        self.assertEqual(r.status_code, 200, r.text)

    def tearDown(self):
        store.set_settings({"architect": None}, "tests")  # the store is shared by every test in the process

    def _agent_answers(self, kind, result):
        """Play the agent: take the next job (there must be one) and report ``result``."""
        job = self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], kind)
        r = self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent)
        self.assertEqual(r.status_code, 200, r.text)
        return job

    def _start(self, **constraints):
        r = self.c.post("/api/v1/architect/sessions", json={"mission": MISSION, "constraints": constraints})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_choosing_the_architect(self):
        self.assertEqual(self.c.put("/api/v1/architect/config", json={"instance_id": self.inst, "profile": "nope"}).status_code, 422)
        api_only = "arc-api-" + os.urandom(3).hex()
        self.c.post("/api/v1/instances", json={"id": api_only, "environment": "lab", "mode": "api-only"})
        self.assertEqual(self.c.put("/api/v1/architect/config", json={"instance_id": api_only, "profile": "default"}).status_code, 409)
        out = self.c.get("/api/v1/architect/config").json()
        self.assertEqual((out["config"]["profile"], out["config"]["model"]["name"]), ("fc-architect", MODEL["name"]))
        mine = next(c for c in out["candidates"] if c["instance_id"] == self.inst)
        self.assertEqual([p["name"] for p in mine["profiles"]], ["default", "fc-architect"])

    def test_from_mission_to_blueprint(self):
        s = self._start(data_residency="on-premises", cloud_models="redacted-only", budget_usd_per_day=5)
        self.assertIsNone(s["latest"])
        self.assertEqual(s["pending"]["version"], 1)
        job = self._agent_answers("hermes_run", {"ok": True, "status": "completed", "run_id": "run_1",
                                                 "output": "Sure:\n```json\n" + json.dumps(PROPOSAL) + "\n```", "usage": {"total_tokens": 900}})
        self.assertEqual(job["params"]["profile"], "fc-architect")
        self.assertIn(MISSION, job["params"]["input"])
        for needle in ("fleetcontrol.proposal/v1", "on-premises", "nous/upstage/solar-pro4:free"):
            self.assertIn(needle, job["params"]["instructions"])

        base = f"/api/v1/architect/sessions/{s['id']}"
        s = self.c.get(base).json()
        self.assertEqual((s["latest"]["version"], len(s["latest"]["proposal"]["agents"]), s["pending"]), (1, 3, None))
        self.assertEqual(s["latest"]["run"]["usage"], {"total_tokens": 900})
        self.assertEqual(self.c.post(f"{base}/blueprint", json={"name": "nothing-accepted"}).status_code, 409)

        r = self.c.patch(f"{base}/agents/invoice-orchestrator", json={"decision": "accepted", "edit": {"role": "Routes every invoice."}})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.c.patch(f"{base}/agents/extractor", json={"decision": "accepted"}).status_code, 200)
        self.assertEqual(self.c.patch(f"{base}/agents/matcher", json={"decision": "removed"}).status_code, 200)
        self.assertEqual(self.c.patch(f"{base}/agents/nobody", json={"decision": "accepted"}).status_code, 404)
        self.assertEqual(self.c.patch(f"{base}/agents/extractor", json={"edit": {"model": {"name": ""}}}).status_code, 422)
        self.assertEqual(self.c.patch(f"{base}/agents/extractor", json={"edit": {"id": "renamed"}}).status_code, 422)

        name = "invoices-" + os.urandom(3).hex()
        r = self.c.post(f"{base}/blueprint", json={"name": name})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["agents"], ["invoice-orchestrator", "extractor"])
        bp = self.c.get(f"/api/v1/blueprints/{name}/1").json()
        self.assertEqual(bp["status"], "draft")
        agents = {a["id"]: a for a in bp["parsed"]["agents"]}
        self.assertEqual(list(agents), ["invoice-orchestrator", "extractor"])
        self.assertEqual(agents["invoice-orchestrator"]["delegates_to"], ["extractor"])
        self.assertEqual(agents["invoice-orchestrator"]["role"], "Routes every invoice.")
        self.assertEqual({p["id"] for p in bp["parsed"]["policies"]},
                         {"data-residency", "cloud-models-redacted", "external-actions-approval", "budget"})
        self.assertEqual(self.c.post(f"{base}/blueprint", json={"name": name}).status_code, 409)
        self.assertEqual(self.c.get(base).json()["blueprint"], {"name": name, "version": 1})
        audit = self.c.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "architect.blueprint_saved" and name in e["target"] for e in audit))

    def test_asking_again_and_answers_that_cannot_be_used(self):
        s = self._start()
        base = f"/api/v1/architect/sessions/{s['id']}"
        self._agent_answers("hermes_run", {"ok": True, "status": "completed", "output": json.dumps(PROPOSAL)})
        self.c.patch(f"{base}/agents/extractor", json={"decision": "accepted"})

        answer = {"question": "Which ERP holds the purchase orders?", "answer": "SAP S/4HANA"}
        self.assertEqual(self.c.post(f"{base}/ask", json={"answers": [answer]}).status_code, 200)
        self.assertEqual(self.c.post(f"{base}/ask", json={}).status_code, 409)  # still working on v2
        job = self._agent_answers("hermes_run", {"ok": True, "status": "completed", "output": "I think you need three agents."})
        self.assertIn("SAP S/4HANA", job["params"]["input"])
        self.assertIn("Keep these agents as they are: extractor.", job["params"]["input"])
        s = self.c.get(base).json()
        self.assertEqual(s["versions"][-1]["status"], "failed")
        self.assertIn("not a JSON object", s["versions"][-1]["error"])
        self.assertEqual(s["versions"][-1]["raw"], "I think you need three agents.")
        self.assertEqual((s["latest"]["version"], s["decisions"]), (1, {"extractor": "accepted"}))  # v1 and the decision stay

        self.assertEqual(self.c.post(f"{base}/ask", json={}).status_code, 200)
        self._agent_answers("hermes_run", {"ok": False, "status": "failed", "error": "provider authentication failed"})
        self.assertEqual(self.c.get(base).json()["versions"][-1]["error"], "provider authentication failed")

    def test_who_may_ask_and_decide(self):
        s = self._start()
        base = f"/api/v1/architect/sessions/{s['id']}"
        self._agent_answers("hermes_run", {"ok": True, "status": "completed", "output": json.dumps(PROPOSAL)})
        self.assertEqual(signed_in("operator").post("/api/v1/architect/sessions", json={"mission": MISSION}).status_code, 403)
        viewer = signed_in("viewer")
        self.assertEqual(viewer.get(base).status_code, 200)
        self.assertEqual(viewer.patch(f"{base}/agents/extractor", json={"decision": "accepted"}).status_code, 403)
        self.assertIn(s["id"], [x["id"] for x in signed_in("fleet_architect").get("/api/v1/architect/sessions").json()])

    def test_the_architect_blueprint(self):
        r = self.c.post("/api/v1/architect/blueprint-asset", json={"instance_id": self.inst})
        self.assertEqual(r.status_code, 201, r.text)
        bp = self.c.get(f"/api/v1/blueprints/{r.json()['name']}/{r.json()['version']}").json()["parsed"]
        agent = bp["agents"][0]
        self.assertEqual((agent["id"], agent["model"]["name"], agent["toolsets"], agent["skills"]), ("fc-architect", MODEL["name"], [], []))
        self.assertEqual(bp["targets"], [{"instance": self.inst, "environment": "lab", "requires_approvals": 0}])


if __name__ == "__main__":
    unittest.main()
