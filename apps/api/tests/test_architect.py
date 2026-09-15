"""Fleet Architect: the proposal contract, what is sent to the architect, and the way to a blueprint."""

import copy
import json
import unittest

from fleetcontrol_api.architect import (
    PROPOSAL_SCHEMA, Constraints, ProposalError, architect_blueprint, check_agent, estate_summary, instructions, merge_edit,
    parse_proposal, request_text, to_blueprint,
)

MODEL = {"provider": "nous", "name": "upstage/solar-pro4:free"}
PROPOSAL = {
    "schema": PROPOSAL_SCHEMA,
    "summary": "An orchestrator with two specialists.",
    "agents": [
        {"id": "invoice-orchestrator", "name": "Invoice Orchestrator", "role": "Routes each invoice.", "model": MODEL,
         "soul": {"objective": "Route every invoice.", "boundaries": ["Never pay anything."]}, "skills": ["pdf"],
         "delegates_to": ["extractor", "matcher"]},
        {"id": "extractor", "role": "Extracts totals and lines.", "model": MODEL, "soul": {"objective": "Extract totals."}, "skills": ["pdf"]},
        {"id": "matcher", "role": "Matches invoices to purchase orders.", "model": MODEL, "soul": {"objective": "Match POs."}},
    ],
    "tests": [{"id": "extracts-total", "target": "extractor", "scenario": "Invoice INV-7 for 1,200 EUR."}],
    "open_questions": ["Which ERP holds the purchase orders?"],
    "estimate": {"cost_per_day_usd": "2-4", "basis": "200 invoices a day"},
}


class ParseTest(unittest.TestCase):
    def test_json_in_prose_or_fences_is_read(self):
        for wrapped in (json.dumps(PROPOSAL), "Here it is:\n```json\n" + json.dumps(PROPOSAL) + "\n```\nThanks.",
                        "Sure. " + json.dumps(PROPOSAL)):
            p = parse_proposal(wrapped)
            self.assertEqual([a["id"] for a in p["agents"]], ["invoice-orchestrator", "extractor", "matcher"])
            self.assertEqual(p["schema"], PROPOSAL_SCHEMA)
            self.assertEqual(p["adjustments"], [])

    def test_what_cannot_be_used_says_why(self):
        cases = {
            "": "empty",
            "I would use three agents.": "not a JSON object",
            json.dumps({**PROPOSAL, "agents": []}): "agents",
            json.dumps({**PROPOSAL, "schema": "fleetcontrol.proposal/v9"}): "v9",
            json.dumps({**PROPOSAL, "agents": [PROPOSAL["agents"][1], PROPOSAL["agents"][1]]}): "repeats",
            json.dumps({**PROPOSAL, "agents": [{**PROPOSAL["agents"][1], "model": {"provider": "nous"}}]}): "model.name",
        }
        for text, why in cases.items():
            with self.assertRaises(ProposalError, msg=text[:40]) as ctx:
                parse_proposal(text)
            self.assertIn(why, str(ctx.exception))

    def test_names_become_ids_and_dangling_references_are_dropped(self):
        p = copy.deepcopy(PROPOSAL)
        p["agents"][0]["id"] = "Invoice Orchestrator"
        p["agents"][0]["delegates_to"] = ["Extractor", "ghost"]
        p["tests"].append({"id": "Ghost Test", "target": "ghost", "scenario": "x"})
        p["open_questions"] = [{"question": "Which ERP?"}]
        p["estimate"]["cost_per_day_usd"] = 3
        out = parse_proposal(json.dumps(p))
        self.assertEqual(out["agents"][0]["id"], "invoice-orchestrator")
        self.assertEqual(out["agents"][0]["delegates_to"], ["extractor"])
        self.assertEqual([t["id"] for t in out["tests"]], ["extracts-total"])
        self.assertEqual(len(out["adjustments"]), 2)
        self.assertEqual((out["open_questions"], out["estimate"]["cost_per_day_usd"]), (["Which ERP?"], "3"))


class AskTest(unittest.TestCase):
    def test_instructions_carry_contract_constraints_and_estate(self):
        estate = estate_summary({"lab": {"a": {"model": MODEL, "skills": ["pdf", "docx"], "toolsets": ["web"]}}, "empty": {}})
        self.assertEqual(estate, {"models": [("nous", "upstage/solar-pro4:free")], "skills": ["docx", "pdf"], "toolsets": ["web"],
                                  "by_instance": {"lab": ["nous/upstage/solar-pro4:free"], "empty": []}})
        text = instructions(Constraints(data_residency="on-premises", cloud_models="redacted-only", budget_usd_per_day=40), estate)
        for needle in (PROPOSAL_SCHEMA, "on-premises", "redacted summaries", "$40 per day", "nous/upstage/solar-pro4:free",
                       "models by instance: lab: nous/upstage/solar-pro4:free", "docx, pdf", "human approval", "never call tools"):
            self.assertIn(needle, text)

    def test_asking_again_sends_the_last_proposal_decisions_and_answers(self):
        text = request_text("Triage invoices.", answers=[{"question": "Which ERP?", "answer": "SAP S/4HANA"}],
                            previous={**PROPOSAL, "adjustments": ["x"]}, decisions={"extractor": "accepted", "matcher": "removed"})
        self.assertIn("Keep these agents as they are: extractor.", text)
        self.assertIn("The person removed: matcher.", text)
        self.assertIn("A: SAP S/4HANA", text)
        self.assertNotIn("adjustments", text)
        self.assertEqual(request_text("Triage invoices.", answers=[], previous=None, decisions={}).count("Mission:"), 1)


class BlueprintTest(unittest.TestCase):
    def test_accepted_agents_become_a_valid_blueprint(self):
        proposal = parse_proposal(json.dumps(PROPOSAL))
        bp = to_blueprint(proposal, name="invoice-triage", owner="dana@example.org", mission="Triage invoices from vendors.",
                          constraints=Constraints(data_residency="on-premises", cloud_models="redacted-only", budget_usd_per_day=5),
                          accepted=["invoice-orchestrator", "extractor"],
                          edits={"extractor": {"model": {"name": "llama-4"}, "skills": ["pdf", "ocr"]}})
        self.assertEqual([a.id for a in bp.agents], ["invoice-orchestrator", "extractor"])
        self.assertEqual(bp.agent("invoice-orchestrator").delegates_to, ["extractor"])  # matcher was not accepted
        self.assertEqual((bp.agent("extractor").model.provider, bp.agent("extractor").model.name), ("nous", "llama-4"))
        self.assertEqual(bp.agent("extractor").tests, ["extracts-total"])
        self.assertEqual({p.id for p in bp.policies}, {"data-residency", "cloud-models-redacted", "external-actions-approval", "budget"})
        self.assertEqual(sorted(bp.managed_fields()), ["extractor", "invoice-orchestrator"])

    def test_edits_merge_and_are_checked(self):
        agent = PROPOSAL["agents"][1]
        self.assertEqual(merge_edit(agent, {"soul": {"boundaries": ["No PII."]}})["soul"],
                         {"objective": "Extract totals.", "boundaries": ["No PII."]})
        with self.assertRaises(ValueError):
            check_agent(merge_edit(agent, {"role": ""}))

    def test_the_architect_blueprint_has_no_tools(self):
        bp = architect_blueprint(MODEL, instance_id="lab-01", environment="lab", owner="me", version=1)
        a = bp.agents[0]
        self.assertEqual((a.id, a.skills, a.toolsets, a.model.name), ("fc-architect", [], [], MODEL["name"]))


if __name__ == "__main__":
    unittest.main()
