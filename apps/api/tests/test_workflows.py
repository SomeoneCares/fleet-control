"""Workflow runs: the state machine, driven through the example blueprint's case-to-sar-draft workflow."""

import os
import unittest

from fleetcontrol_blueprint import load_blueprint

from fleetcontrol_api.workflows import (
    WorkflowError, compose_input, current_step, decide_gate, escalate_due, finish_room_step, instructions,
    may_decide_gate, missing_requirements, new_run, pending_members, progress, question_for, record_result, run_row,
    settle, start_step,
)

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


def example_run(at=1000.0):
    bp = load_blueprint(EXAMPLE)
    wf = bp.workflows[0]
    steps = [s.model_dump(mode="json", exclude_none=True) for s in wf.steps]
    return new_run(run_id="wfr_1", blueprint=bp.metadata.name, version=bp.metadata.version, workflow_id=wf.id,
                   instance_id="lab-01", steps=steps, started_by="sam@x", at=at, input_text="Case AML-2026-0412: wires to Limassol")


class RunTest(unittest.TestCase):
    def test_the_example_workflow_reads_as_six_steps(self):
        run = example_run()
        self.assertEqual([s["kind"] for s in run["steps"]], ["agent", "parallel", "agent", "human_gate", "agent", "decision_room"])
        self.assertEqual(run["steps"][3]["on_reject"], "challenger")
        self.assertEqual(progress(run), {"done": 0, "total": 6, "current": 0, "current_label": "case-orchestrator"})

    def test_a_run_goes_through_gate_send_back_and_room(self):
        run = example_run()
        start_step(run["steps"][0], 1001)
        record_result(run, 0, "case-orchestrator", ok=True, output="Brief: four entities, Limassol correspondent.", at=1002)
        self.assertEqual(run["steps"][0]["status"], "done")
        para = run["steps"][1]
        start_step(para, 1003)
        record_result(run, 1, "sanctions-screener", ok=True, output="0 matches", at=1004)
        self.assertEqual((para["status"], [m["agent"] for m in pending_members(para)]), ("running", ["ownership-tracer"]))
        record_result(run, 1, "ownership-tracer", ok=True, output="UBO: J. Doe", at=1005)
        self.assertEqual(para["status"], "done")

        challenger_input = compose_input(run, run["steps"][2]["members"][0], run["steps"][2])
        for text in ("Request: Case AML-2026-0412", "screening-result.json:\n0 matches", "UBO: J. Doe", "Produce: challenge-memo"):
            self.assertIn(text, challenger_input)  # the reviewer reads what the others produced, not only their names
        self.assertIn("challenge-memo.md itself", instructions(run, run["steps"][2]["members"][0], run["steps"][2]))
        start_step(run["steps"][2], 1006)
        record_result(run, 2, "challenger", ok=True, output="Memo v1", at=1007)

        gate = run["steps"][3]
        start_step(gate, 1008)
        self.assertEqual(run_row(run, now=1009)["awaiting_role"], "approver")
        with self.assertRaises(WorkflowError):
            decide_gate(run, 3, by="op@x", role="operator", approve=True)
        decide_gate(run, 3, by="lena@x", role="approver", approve=False, note="Check the second wire leg", at=1010)
        # sent back: the challenger runs again with the note, and everything after it follows again
        self.assertEqual((run["status"], current_step(run)["index"], run["steps"][2]["note"]), ("running", 2, "Check the second wire leg"))
        self.assertIn("sent this back to you with this note: Check the second wire leg",
                      compose_input(run, run["steps"][2]["members"][0], run["steps"][2]))
        start_step(run["steps"][2], 1011)
        record_result(run, 2, "challenger", ok=True, output="Memo v2", at=1012)
        start_step(gate, 1013)
        decide_gate(run, 3, by="lena@x", role="approver", approve=True, at=1014)
        start_step(run["steps"][4], 1015)
        record_result(run, 4, "sar-drafter", ok=True, output="SAR draft", at=1016)
        self.assertEqual(run["artifacts"]["sar-draft.docx"]["agent"], "sar-drafter")
        self.assertEqual(question_for(run, "File a SAR for {case}?", {"case": "AML-1"}), "File a SAR for AML-1?")
        finish_room_step(run, 5, "room_9", at=1017)
        settle(run, at=1018)
        self.assertEqual((run["status"], run["room_id"], progress(run)["done"]), ("done", "room_9", 6))

    def test_a_member_that_fails_fails_the_run_and_says_who(self):
        run = example_run()
        start_step(run["steps"][0], 1)
        record_result(run, 0, "case-orchestrator", ok=False, error="provider down", at=2)
        self.assertEqual((run["status"], run["error"]), ("failed", "case-orchestrator did not finish"))

    def test_an_overdue_gate_escalates_once_and_then_its_target_may_decide(self):
        run = example_run()
        gate = run["steps"][3]
        start_step(gate, 1)
        self.assertEqual(escalate_due(run, now=3600), [])  # 1 h of a 24 h timeout
        self.assertFalse(may_decide_gate(gate, email="m@x", role="mlro")[0])
        self.assertEqual([s["index"] for s in escalate_due(run, now=25 * 3600)], [3])
        self.assertEqual(escalate_due(run, now=26 * 3600), [])  # once
        self.assertTrue(may_decide_gate({**gate, "escalate_to": "mlro"}, email="x@x", role="mlro")[0])
        self.assertTrue(may_decide_gate({**gate, "escalate_to": "boss@x"}, email="boss@x", role="viewer")[0])
        self.assertTrue(run_row(run, now=25 * 3600)["overdue"])

    def test_missing_agents_and_mcp_servers_are_named(self):
        run = example_run()
        agents = {"case-orchestrator": {"mcps": ["case-store"]}, "sanctions-screener": {"mcps": ["opensanctions"]}}
        problems = missing_requirements(run["steps"], agents, available_mcps=["opensanctions"])
        self.assertIn("case-orchestrator needs the MCP server 'case-store', which this instance does not have", problems)
        self.assertIn("ownership-tracer is not an agent in this blueprint", problems)


if __name__ == "__main__":
    unittest.main()
