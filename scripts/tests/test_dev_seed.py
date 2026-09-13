"""The demo seeder's simulated agent must behave like fleetctl-agent: apply writes, drift scan compares."""

import unittest

import dev_seed
from fleetcontrol_blueprint import load_blueprint


class SimulatedAgentTest(unittest.TestCase):
    def setUp(self):
        self.bp = load_blueprint(dev_seed.EXAMPLE)
        self.managed = self.bp.managed_fields()
        self.agent = dev_seed.SimulatedAgent("x", "tok", {}, {})

    def test_apply_then_scan_leaves_only_mcps(self):
        from fleetcontrol_api.planner import compute_plan, to_agent_job

        plan = compute_plan(self.bp, {}, target_instance="x", agent_installed=True, environment="lab")
        ops = to_agent_job(plan)[1]["params"]
        self.assertTrue(self.agent.handle("apply", ops)["ok"])
        drift = self.agent.handle("drift_scan", {"managed": self.managed})["drift"]
        # MCP servers are a manual step in Slice 1 (the blueprint names them; registering needs their config),
        # so a freshly created profile still differs there and nowhere else.
        self.assertEqual({d["field"] for diffs in drift.values() for d in diffs}, {"mcps"})

    def test_scan_reports_manual_change_and_missing_profile(self):
        self.agent.profiles = {k: dict(v) for k, v in self.managed.items() if k != "challenger"}
        self.agent.profiles["sanctions-screener"]["skills"] = ["quick-lookup"]
        drift = self.agent.handle("drift_scan", {"managed": self.managed})["drift"]
        self.assertEqual(drift["challenger"][0]["field"], "profile")
        self.assertEqual(drift["sanctions-screener"][0]["field"], "skills")

    def test_import_includes_soul_text(self):
        self.agent.profiles = {"p": {"skills": []}}
        self.agent.souls = {"p": "# Objective\n"}
        self.assertEqual(self.agent.handle("import_profiles", {})["profiles"]["p"]["soul_text"], "# Objective\n")


if __name__ == "__main__":
    unittest.main()
