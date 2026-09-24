"""Integrations: what the instances have, merged with what the blueprints say may use it."""

import unittest

from fleetcontrol_api.integrations import aggregate, mcp_config, merge_discovery

INSTANCES = [{"id": "prod-01", "environment": "production"}, {"id": "lab-01", "environment": "lab"}]
LIVE = {
    "prod-01": {"screener": {"model": {"provider": "local", "name": "llama-4"}, "mcps": ["opensanctions", "sas-viya"]}},
    "lab-01": {"screener": {"model": {"provider": "anthropic", "name": "claude-sonnet"}, "mcps": ["opensanctions"]}},
}
DISCOVERED = {
    "prod-01": {"screener": [{"name": "opensanctions", "enabled": True, "transport": "http", "url": "https://os.example/mcp", "ok": True,
                              "tools": [{"name": "search", "description": "Search the lists"}]},
                             {"name": "sas-viya", "enabled": True, "ok": False, "error": "connection refused", "tools": []}]},
    "lab-01": {"screener": [{"name": "opensanctions", "enabled": False}]},
}
BLUEPRINT = {
    "metadata": {"name": "aml", "version": 2},
    "agents": [{"id": "screener", "mcps": ["opensanctions"], "model": {"provider": "local", "name": "llama-4"}},
               {"id": "drafter", "mcps": [], "model": {"provider": "anthropic", "name": "claude-sonnet"}}],
    "policies": [{"id": "no-export", "kind": "tool-denylist", "applies_to": ["drafter"], "enforcement": "block",
                  "params": {"tools": ["sas-viya.data_export"]}},
                 {"id": "external", "kind": "external-action-approval", "applies_to": ["*"], "enforcement": "approve",
                  "params": {"tools": ["opensanctions.submit"]}}],
}


class AggregateTest(unittest.TestCase):
    def setUp(self):
        self.rows = {(r["kind"], r["name"]): r for r in aggregate(instances=INSTANCES, live=LIVE, discovered=DISCOVERED, blueprints=[BLUEPRINT])}

    def test_mcp_servers_carry_health_tools_and_users(self):
        os_row = self.rows[("mcp", "opensanctions")]
        self.assertEqual((os_row["instances"], os_row["environments"]), (["lab-01", "prod-01"], ["lab", "production"]))
        self.assertEqual([t["name"] for t in os_row["tools"]], ["search"])
        self.assertEqual(os_row["used_by"], [{"agent": "screener", "blueprint": "aml", "version": 2}])
        self.assertEqual((os_row["health"], os_row["enabled_everywhere"], os_row["endpoint"]), ("healthy", False, "https://os.example/mcp"))
        self.assertEqual(os_row["allow"]["opensanctions.submit"]["approval_for"], ["screener", "drafter"])

    def test_a_server_that_does_not_answer_is_unreachable(self):
        viya = self.rows[("mcp", "sas-viya")]
        self.assertEqual((viya["health"], viya["error"]), ("unreachable", "connection refused"))
        self.assertEqual(viya["used_by"], [])  # no blueprint agent uses it
        self.assertEqual(viya["allow"]["sas-viya.data_export"]["blocked_for"], ["drafter"])

    def test_model_providers_come_from_the_profiles_and_the_blueprints(self):
        local = self.rows[("model", "local")]
        self.assertEqual((local["models"], local["instances"], local["health"]), (["llama-4"], ["prod-01"], "healthy"))
        self.assertEqual({u["agent"] for u in self.rows[("model", "anthropic")]["used_by"]}, {"drafter"})

    def test_ordering_puts_mcp_servers_first(self):
        kinds = [r["kind"] for r in aggregate(instances=INSTANCES, live=LIVE, discovered=DISCOVERED, blueprints=[BLUEPRINT])]
        self.assertEqual(kinds, sorted(kinds, key=lambda k: k != "mcp"))

    def test_one_bad_profile_makes_a_server_degraded_not_unreachable(self):
        live = {"lab-01": {p: {"mcps": ["sas-viya"]} for p in ("analyst", "reviewer", "writer")}}
        found = {"lab-01": {"analyst": [{"name": "sas-viya", "ok": True, "tools": []}],
                            "reviewer": [{"name": "sas-viya", "ok": False, "error": "token expired"}],
                            "writer": [{"name": "sas-viya", "enabled": False}]}}
        row = aggregate(instances=INSTANCES, live=live, discovered=found, blueprints=[])[0]
        self.assertEqual(row["health"], "degraded")
        self.assertIn("1 of 2 profiles cannot reach it (reviewer): token expired", row["error"])
        self.assertEqual([(e["profile"], e["health"]) for e in row["profile_health"]],
                         [("analyst", "healthy"), ("reviewer", "unreachable"), ("writer", "disabled")])

    def test_a_blank_error_is_not_left_blank(self):
        found = {"lab-01": {"analyst": [{"name": "sas-viya", "ok": False, "error": ""}]}}
        row = aggregate(instances=INSTANCES, live={}, discovered=found, blueprints=[])[0]
        self.assertEqual(row["health"], "unreachable")
        self.assertIn("did not answer", row["error"])

    def test_users_come_from_the_applied_version_and_drafts_only_plan(self):
        applied = {"metadata": {"name": "aml", "version": 2}, "agents": [{"id": "screener", "mcps": ["opensanctions"]}]}
        draft = {"metadata": {"name": "aml", "version": 3}, "agents": [{"id": "screener", "mcps": ["opensanctions"]},
                                                                      {"id": "analyst", "mcps": ["opensanctions"]}]}
        row = next(r for r in aggregate(instances=INSTANCES, live=LIVE, discovered={}, blueprints=[applied], drafts=[draft])
                   if r["name"] == "opensanctions")
        self.assertEqual(row["used_by"], [{"agent": "screener", "blueprint": "aml", "version": 2}])
        self.assertEqual(row["planned_by"], [{"agent": "analyst", "blueprint": "aml", "version": 3}])  # screener is not news

    def test_the_newer_of_import_and_discovery_says_which_profiles_have_a_server(self):
        live = {"lab-01": {"analyst": {"mcps": ["sas-viya"]}, "writer": {"mcps": ["sas-viya"]}}}
        found = {"lab-01": {"analyst": [{"name": "sas-viya", "ok": True}], "writer": []}}  # deregistered from writer
        rows = aggregate(instances=INSTANCES, live=live, discovered=found, blueprints=[],
                         live_at={"lab-01": 10.0}, discovered_at={"lab-01": {"analyst": 20.0, "writer": 20.0}})
        self.assertEqual(rows[0]["profiles"], ["lab-01/analyst"])
        # an import newer than the discovery wins the other way: re-registered on writer, not yet probed there
        rows = aggregate(instances=INSTANCES, live=live, discovered=found, blueprints=[],
                         live_at={"lab-01": 30.0}, discovered_at={"lab-01": {"analyst": 20.0, "writer": 20.0}})
        self.assertEqual(rows[0]["profiles"], ["lab-01/analyst", "lab-01/writer"])
        self.assertEqual([e["health"] for e in rows[0]["profile_health"]], ["healthy", "unknown"])

    def test_a_partial_discovery_merges(self):
        before = {"analyst": {"at": 1.0, "servers": [{"name": "a"}]}, "writer": {"at": 1.0, "servers": [{"name": "a"}]},
                  "gone": {"at": 1.0, "servers": []}}
        after = merge_discovery(before, {}, 2.0, covered=["writer"], keep={"analyst", "writer"})
        self.assertEqual(after, {"analyst": {"at": 1.0, "servers": [{"name": "a"}]}, "writer": {"at": 2.0, "servers": []}})

    def test_nothing_known_yet(self):
        self.assertEqual(aggregate(instances=[], live={}, discovered={}, blueprints=[]), [])


class McpConfigTest(unittest.TestCase):
    def test_a_server_is_added_by_url_or_by_command_and_never_with_secrets(self):
        self.assertEqual(mcp_config("os", url="https://os.example/mcp", auth="oauth"),
                         {"name": "os", "url": "https://os.example/mcp", "auth": "oauth"})
        self.assertEqual(mcp_config("local", command="npx", args=["-y", "server"]),
                         {"name": "local", "command": "npx", "args": ["-y", "server"]})
        for bad in ({}, {"url": "u", "command": "c"}):
            with self.assertRaises(ValueError):
                mcp_config("x", **bad)


if __name__ == "__main__":
    unittest.main()
