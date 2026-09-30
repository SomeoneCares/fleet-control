"""The mission library: every pack is a valid blueprint and keeps people in control."""

import unittest

from fleetcontrol_blueprint.missions import SECTORS, expand, get_pack, instantiate, library, load_pack, sas_viya_tools

MODEL = {"provider": "nous", "name": "upstage/solar-pro4:free"}
# SAS tools no agent may ever call, whatever a pack says: they publish, lock, delete or run arbitrary code
NEVER = {"execute_sas_code", "submit_batch_job", "publish_decision_flow", "lock_decision_flow_revision",
         "lock_business_ruleset_revision", "delete_decision_flow", "delete_business_ruleset", "delete_business_rule",
         "publish_ml_champion_model", "delete_report", "delete_glossary_term"}


def _tool(name: str) -> str:
    return name.split(".", 1)[1] if name.startswith("sas-viya.") else name


class CatalogueTest(unittest.TestCase):
    def test_sas_viya_catalogue_is_complete(self):
        tools = sas_viya_tools()
        self.assertEqual(len(tools), 92)
        by_access = {a: sum(1 for t in tools.values() if t["access"] == a) for a in ("read-only", "write", "destructive")}
        self.assertEqual(by_access, {"read-only": 51, "write": 20, "destructive": 21})


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.packs = library()

    def test_three_missions_per_sector(self):
        for sector in SECTORS:
            self.assertEqual(len([p for p in self.packs if p.sector == sector]), 3, sector)

    def test_ids_unique(self):
        ids = [p.id for p in self.packs]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_pack_expands_to_a_valid_blueprint(self):
        for p in self.packs:
            bp = expand(p)
            self.assertEqual(bp.metadata.labels["mission"], p.id)
            self.assertTrue(bp.agents)

    def test_named_sas_tools_exist(self):
        known = sas_viya_tools()
        for p in self.packs:
            bp = expand(p)
            names = set(p.sas.mcp_tools)
            names |= {_tool(t) for pol in bp.policies for t in (pol.params or {}).get("tools", []) if t.startswith("sas-viya.")}
            names |= {_tool(t) for t in [x for test in bp.tests for x in test.required_tools + test.forbidden_tools]
                      if t.startswith("sas-viya.")}
            unknown = sorted(n for n in names if n not in known)
            self.assertEqual(unknown, [], p.id)

    def test_sas_agents_are_read_only_except_approved_tools(self):
        """Every write-class SAS tool is either denied or needs a person's approval, for every agent."""
        writes = {n for n, t in sas_viya_tools().items() if t["access"] != "read-only"}
        for p in self.packs:
            bp = expand(p)
            for a in bp.agents:
                if "sas-viya" not in a.mcps:
                    continue
                mine = [pol for pol in bp.policies if "*" in pol.applies_to or a.id in pol.applies_to]
                denied = {_tool(t) for pol in mine if pol.enforcement == "block" for t in pol.params.get("tools", [])}
                approved = {_tool(t) for pol in mine if pol.enforcement == "approve" for t in pol.params.get("tools", [])}
                self.assertEqual(writes - denied - approved, set(), f"{p.id}/{a.id}")
                self.assertEqual(denied & approved, set(), f"{p.id}/{a.id}: a tool is both denied and approved")
                self.assertEqual(approved & NEVER, set(), f"{p.id}/{a.id}: a never-tool is merely approved")

    def test_every_workflow_waits_for_a_person(self):
        for p in self.packs:
            bp = expand(p)
            self.assertTrue(bp.workflows, p.id)
            for wf in bp.workflows:
                gates = [s for s in wf.steps if getattr(s, "human_gate", None)]
                self.assertTrue(gates, f"{p.id}/{wf.id} has no human gate")
                self.assertTrue(all(g.on_reject for g in gates), f"{p.id}/{wf.id}: a gate cannot send work back")

    def test_no_pack_carries_a_model_or_a_secret(self):
        for p in self.packs:
            for a in p.blueprint.get("agents", []):
                self.assertNotIn("model", a, f"{p.id}/{a['id']}: the model is chosen at provisioning")
            self.assertFalse(p.blueprint.get("secrets"), p.id)

    def test_connector_missions_name_their_connectors(self):
        for p in self.packs:
            if p.sas.reach != "mcp-today":
                self.assertTrue(p.sas.connectors, p.id)
            used = {m for a in p.blueprint["agents"] for m in a.get("mcps", [])} - {"sas-viya"}
            self.assertEqual(used, set(p.sas.connectors), p.id)

    def test_list_items_are_whole(self):
        """An inline YAML list splits on commas: a sentence cut in two would reach the agent's SOUL broken."""
        for p in self.packs:
            for a in p.blueprint["agents"]:
                for key in ("principles", "boundaries"):
                    for s in a["soul"].get(key, []):
                        self.assertTrue(s.endswith("."), f"{p.id}/{a['id']} {key}: {s!r}")
            for v in p.market.vendors + p.kpis + p.sas.modules:
                self.assertEqual(v.count("("), v.count(")"), f"{p.id}: {v!r}")

    def test_researched_markets_cite_sources(self):
        for p in self.packs:
            if p.market.saturation != "unknown" and p.market.vendors:
                self.assertTrue(p.market.sources, p.id)


class InstantiateTest(unittest.TestCase):
    def test_instantiate_sets_owner_model_and_target(self):
        bp = instantiate(get_pack("credit-early-warning"), owner="dana@bank.test", model=MODEL, name="cew-emea",
                         version=3, target={"instance": "lab-01", "environment": "lab"})
        self.assertEqual((bp.metadata.name, bp.metadata.version, bp.metadata.owner), ("cew-emea", 3, "dana@bank.test"))
        self.assertTrue(all(a.model.name == MODEL["name"] for a in bp.agents))
        self.assertEqual(bp.targets[0].instance, "lab-01")

    def test_instantiate_needs_a_model(self):
        with self.assertRaises(ValueError):
            instantiate(get_pack("credit-early-warning"), owner="x@y.test", model={})

    def test_approve_sets_leave_the_deny_list(self):
        bp = expand(get_pack("decision-strategy-change"))
        deny = next(p for p in bp.policies if p.id == "sas-viya-read-only").params["tools"]
        self.assertNotIn("sas-viya.score_data", deny)
        self.assertIn("sas-viya.publish_decision_flow", deny)

    def test_unknown_policy_set_is_refused(self):
        pack = load_pack(get_pack("kpi-anomaly-investigation").model_dump_json())
        pack.include_policies.append("no-such-set")
        with self.assertRaises(ValueError):
            expand(pack)


if __name__ == "__main__":
    unittest.main()
