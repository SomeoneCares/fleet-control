"""Effective access: person ∩ agent ∩ system, each line with the rule behind it."""

import unittest

from fleetcontrol_api.access import (
    PERMISSION_LABEL, agent_grants, combined_zones, person_permissions, person_zones, tool_verdict, why_apply, why_room,
)
from fleetcontrol_api.auth import PERMISSIONS

ZONES = [{"id": "case-files", "read_roles": ["approver", "viewer"]}, {"id": "ops-reports", "read_roles": ["operator"]},
         {"id": "board", "read_roles": []}]
BP = {"metadata": {"name": "aml", "version": 3},
      "agents": [{"id": "screener", "content_zones": ["case-files", "ops-reports"], "mcps": ["opensanctions"], "toolsets": ["web"]}],
      "policies": [{"id": "no-web-fetch", "applies_to": ["screener"], "enforcement": "block", "params": {"tools": ["web.fetch"]}},
                   {"id": "external", "applies_to": ["*"], "enforcement": "approve", "params": {"tools": ["opensanctions.submit"]}}]}


class PersonTest(unittest.TestCase):
    def test_every_permission_is_explained(self):
        self.assertEqual(set(PERMISSION_LABEL), set(PERMISSIONS))  # a new permission needs words here

    def test_what_a_role_may_do_and_why_not(self):
        perms = {p["permission"]: p for p in person_permissions("approver")}
        self.assertTrue(perms["rooms.decide"]["allowed"])
        self.assertEqual(perms["rooms.decide"]["why"], "the Approver role has it")
        self.assertFalse(perms["plans.apply.production"]["allowed"])
        self.assertEqual(perms["plans.apply.production"]["why"], "only Admin, Operator")

    def test_zones_follow_the_readers_and_admins_read_everything(self):
        z = {x["zone"]: x for x in person_zones("approver", ZONES)}
        self.assertEqual((z["case-files"]["allowed"], z["ops-reports"]["allowed"], z["board"]["why"]),
                         (True, False, "readers: Admins only"))
        self.assertTrue(all(x["allowed"] for x in person_zones("admin", ZONES)))


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.g = agent_grants(BP, "screener")

    def test_grants_and_the_policies_that_hold_it_back(self):
        self.assertEqual((self.g["mcps"], self.g["blocked"][0]["policy"], self.g["approval"][0]["tool"]),
                         (["opensanctions"], "no-web-fetch", "opensanctions.submit"))
        self.assertIsNone(agent_grants(BP, "nobody"))

    def test_a_tool_is_decided_by_policy_then_the_agent_then_the_system(self):
        servers = {"opensanctions": {"auth": "oauth", "health": "healthy"}, "sas-viya": {"auth": "oauth"}}
        blocked = tool_verdict(self.g, "web.fetch", servers=servers)
        self.assertEqual((blocked["allowed"], blocked["layer"]), (False, "policy"))
        ok = tool_verdict(self.g, "opensanctions.search", servers=servers)
        self.assertEqual((ok["allowed"], ok["layer"], ok["needs_approval"]), (True, "system", False))
        self.assertIn("OAuth", ok["system"])  # the system decides the rest, and the inspector says so
        self.assertTrue(tool_verdict(self.g, "opensanctions.submit", servers=servers)["needs_approval"])
        self.assertIn("not among the agent's MCP servers", tool_verdict(self.g, "sas-viya.list_caslibs", servers=servers)["why"])
        self.assertTrue(tool_verdict(self.g, "web.search", servers=servers)["allowed"])  # the web toolset, not blocked
        self.assertFalse(tool_verdict(self.g, "terminal.run", servers=servers)["allowed"])

    def test_together_is_only_what_both_may_read(self):
        both = {z["zone"]: z for z in combined_zones(person_zones("approver", ZONES), self.g)}
        self.assertEqual((both["case-files"]["allowed"], both["ops-reports"]["allowed"]), (True, False))
        self.assertIn("the person may not", both["ops-reports"]["why"])
        self.assertNotIn("board", both)  # neither reads it: nothing to say


class WhyTest(unittest.TestCase):
    ROOM = {"id": "r1", "zone": "case-files", "status": "open", "decisions": [{"by": "first@x"}], "second_approver": "ali@x"}

    def test_room_rules_in_the_order_the_api_applies_them(self):
        self.assertIn("cannot read zone", why_room(self.ROOM, email="o@x", role="operator", zones=ZONES)["why"])
        self.assertEqual(why_room(self.ROOM, email="dana@x", role="approver", zones=ZONES)["why"], "Waiting for the second approver (ali@x)")
        self.assertTrue(why_room(self.ROOM, email="ali@x", role="approver", zones=ZONES)["allowed"])

    def test_applying_says_who_applies_and_who_approves(self):
        self.assertFalse(why_apply("production", "approver")["allowed"])
        self.assertIn("approve: yes", why_apply("production", "approver")["why"])
        self.assertTrue(why_apply("lab", "operator")["allowed"])


if __name__ == "__main__":
    unittest.main()
