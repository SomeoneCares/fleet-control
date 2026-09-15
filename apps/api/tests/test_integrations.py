"""Integrations: what the instances have, merged with what the blueprints say may use it."""

import unittest

from fleetcontrol_api.integrations import aggregate, mcp_config

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
    "metadata": {"name": "aml"},
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
        self.assertEqual(os_row["used_by"], [{"agent": "screener", "blueprint": "aml"}])
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
