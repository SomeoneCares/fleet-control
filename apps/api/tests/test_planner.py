import os, unittest
from fleetcontrol_blueprint import load_blueprint
from fleetcontrol_api.planner import compute_plan, mcp_sources, to_agent_job

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

    def test_mcp_servers_are_reconciled_by_copying_from_another_profile(self):
        live = {k: dict(v) for k, v in self.desired.items()}
        live["sanctions-screener"]["mcps"] = ["scratch-pad"]  # opensanctions was deregistered, scratch-pad added by hand
        live["ownership-tracer"]["mcps"] = ["corporate-registry", "opensanctions"]  # has opensanctions: a source
        sources = mcp_sources(live, {"opensanctions": "oauth"})
        plan = compute_plan(self.bp, live, target_instance="lab-01", agent_installed=True, environment="lab", sources=sources)
        row = next(r for r in plan["changes"] if r["object"] == "profile sanctions-screener")
        self.assertIn("MCP servers: +opensanctions, −scratch-pad", row["description"])
        self.assertIn({"op": "copy_mcp", "profile": "sanctions-screener", "server": "opensanctions", "from_profile": "ownership-tracer"}, row["ops"])
        self.assertIn({"op": "remove_mcp", "profile": "sanctions-screener", "server": "scratch-pad"}, row["ops"])
        # an extra server the blueprint does not declare is removed from ownership-tracer too
        tracer = next(r for r in plan["changes"] if r["object"] == "profile ownership-tracer")
        self.assertEqual([o["op"] for o in tracer["ops"]], ["remove_mcp"])
        # OAuth tokens are per profile: the new registration needs its own login, on the host
        self.assertEqual(plan["manual_steps"], [{"profile": "sanctions-screener", "server": "opensanctions",
                         "step": "hermes -p sanctions-screener mcp login opensanctions --flow browser, then hermes gateway restart"}])
        self.assertTrue(plan["can_apply"])
        self.assertEqual(plan["warnings"], [])

    def test_a_server_no_profile_has_is_reported_not_invented(self):
        plan = compute_plan(self.bp, {}, target_instance="fresh-01", agent_installed=True, environment="lab")
        self.assertTrue(plan["can_apply"])  # the profiles can still be created
        self.assertFalse([o for r in plan["changes"] for o in r["ops"] if o["op"] == "copy_mcp"])
        self.assertEqual(len(plan["warnings"]), 1)
        for server in ("case-store", "opensanctions", "corporate-registry", "document-store"):
            self.assertIn(server, plan["warnings"][0])
        # with the server configured on some other profile of the instance, the create registers it
        plan = compute_plan(self.bp, {}, target_instance="lab-01", agent_installed=True, environment="lab",
                            sources={"opensanctions": {"profile": "default", "auth": None}})
        ops = next(r for r in plan["changes"] if r["object"] == "profile sanctions-screener")["ops"]
        self.assertEqual(ops[-1], {"op": "copy_mcp", "profile": "sanctions-screener", "server": "opensanctions", "from_profile": "default"})
        self.assertEqual(plan["manual_steps"], [])

    def test_the_organisation_floor_and_the_blueprint_target(self):
        live = {k: dict(v) for k, v in self.desired.items()}
        floor = lambda prod, stg=0: {"production": prod, "staging": stg, "lab": 0}
        # the example's production target asks for 2: a floor of 3 raises it, a floor of 1 cannot lower it
        for prod, want in ((3, 3), (1, 2)):
            plan = compute_plan(self.bp, live, target_instance="hermes-prod-eu-01", agent_installed=True,
                                environment="production", default_approvals=floor(prod))
            self.assertEqual(plan["approvals_required"], want)
        plan = compute_plan(self.bp, live, target_instance="some-staging", agent_installed=True, environment="staging",
                            default_approvals=floor(2, 1))
        self.assertEqual(plan["approvals_required"], 1)

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
