import os, unittest
from fleetcontrol_blueprint import load_blueprint
from fleetcontrol_api.planner import compute_plan, to_agent_job

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


class PlannerTest(unittest.TestCase):
    def setUp(self):
        self.bp = load_blueprint(EXAMPLE)
        self.desired = self.bp.managed_fields()

    def test_empty_instance_creates_every_profile(self):
        plan = compute_plan(self.bp, {}, target_instance="hermes-staging-eu-01", agent_installed=True, environment="staging")
        creates = [r for r in plan["changes"] if r["kind"] == "create"]
        self.assertEqual(len(creates), 5)
        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["approvals_required"], 0)
        self.assertEqual(plan["policy_push"]["sanctions-screener"]["deny_tools"], ["web.fetch"])
        self.assertEqual(plan["policy_push"]["case-orchestrator"]["approve_tools"], ["http.post", "sar.submit"])
        # new profiles come with Hermes's defaults on; the create syncs to exactly the blueprint's lists
        ops = creates[0]["ops"]
        self.assertIn({"op": "sync_skills", "profile": "case-orchestrator",
                       "skills": ["case-routing", "evidence-ledger", "redaction"]}, ops)
        self.assertIn({"op": "sync_toolsets", "profile": "case-orchestrator", "toolsets": []}, ops)
        self.assertFalse([o for o in ops if o["op"] in ("set_skill", "set_toolset")])

    def test_in_sync_instance_has_no_changes(self):
        live = {k: dict(v) for k, v in self.desired.items()}
        plan = compute_plan(self.bp, live, target_instance="hermes-prod-eu-01", agent_installed=True, environment="production")
        self.assertEqual([r for r in plan["changes"] if r["kind"] in ("create", "update")], [])
        self.assertEqual(plan["approvals_required"], 2)
        self.assertEqual(len(plan["no_change"]["profiles"]), 5)

    def test_skill_and_soul_drift_produce_update_row_with_ops(self):
        live = {k: dict(v) for k, v in self.desired.items()}
        live["sanctions-screener"]["skills"] = ["name-matching", "quick-lookup"]
        live["sanctions-screener"]["soul_sha256"] = "sha256:old"
        live["unmanaged-bot"] = {"skills": []}
        plan = compute_plan(self.bp, live, target_instance="hermes-staging-eu-01", agent_installed=True, environment="staging")
        upd = [r for r in plan["changes"] if r["object"] == "profile sanctions-screener"]
        self.assertEqual(len(upd), 1)
        self.assertIn("+list-versioning", upd[0]["description"])
        self.assertIn("−quick-lookup", upd[0]["description"])
        self.assertIn("SOUL", upd[0]["description"])
        ops = {(o["op"], o.get("skill")) for o in upd[0]["ops"]}
        self.assertIn(("set_skill", "quick-lookup"), ops)
        self.assertIn(("write_soul", None), ops)
        self.assertEqual(plan["unmanaged_profiles"], ["unmanaged-bot"])

    def test_production_needs_two_approvals_even_when_not_a_target(self):
        plan = compute_plan(self.bp, {}, target_instance="hermes-prod-us-02", agent_installed=True, environment="production")
        self.assertEqual(plan["approvals_required"], 2)
        lab = compute_plan(self.bp, {}, target_instance="hermes-lab-09", agent_installed=True, environment="lab")
        self.assertEqual(lab["approvals_required"], 0)

    def test_api_only_instance_cannot_apply(self):
        plan = compute_plan(self.bp, {}, target_instance="hermes-lab-01", agent_installed=False, environment="lab")
        self.assertFalse(plan["can_apply"])
        self.assertIn("install the fleet control agent", plan["blocked_reason"].lower())

    def test_agent_jobs_order(self):
        plan = compute_plan(self.bp, {}, target_instance="hermes-staging-eu-01", agent_installed=True, environment="staging")
        jobs = to_agent_job(plan)
        self.assertEqual([j["kind"] for j in jobs], ["push_policy", "apply"])
        self.assertTrue(jobs[1]["params"]["snapshot"])
        self.assertEqual(jobs[1]["params"]["changes"][0]["op"], "ensure_profile")


if __name__ == "__main__":
    unittest.main()
