"""Decision Rooms: what a room may be, and how decisions close it."""

import unittest

from fleetcontrol_api.rooms import (
    RoomError, check_tool, decide, is_complete, may_decide, new_evidence, new_finding, new_room, outcome, room_row, visible, waiting_for,
)

OPTIONS = ["File SAR draft for signature", "Request more evidence", "Close as legitimate transfer"]


def room(**kw):
    base = {"room_id": "room_1", "question": "Should we file a SAR for Alpha Trading Ltd?", "zone": "case-files",
            "options": OPTIONS, "opened_by": "case-orchestrator", "opened_by_kind": "agent", "at": 100.0,
            "case": "AML-2026-0412"}
    base.update(kw)
    return new_room(**base)


class RoomShapeTest(unittest.TestCase):
    def test_a_room_asks_one_question_with_options(self):
        r = room()
        self.assertEqual([o["id"] for o in r["options"]],
                         ["file-sar-draft-for-signature", "request-more-evidence", "close-as-legitimate-transfer"])
        self.assertEqual((r["status"], r["case"], r["decisions"]), ("open", "AML-2026-0412", []))

    def test_what_a_room_may_not_be(self):
        for bad in ({"question": "Too short"}, {"options": ["Only one"]}, {"options": OPTIONS * 3},
                    {"options": ["File it", "file  it"]}, {"opened_by_kind": "robot"}):
            with self.assertRaises(RoomError, msg=str(bad)):
                room(**bad)

    def test_evidence_and_findings_carry_their_verdicts(self):
        e = new_evidence(kind="claim", label="Called opensanctions.search", ref="tr_1:0", verdict="Evidence found",
                         added_by="dana@example.org", at=1.0)
        self.assertEqual((e["kind"], e["verdict"]), ("claim", "Evidence found"))
        self.assertEqual(new_evidence(kind="note", label="Board minutes were not verified", added_by="x", at=1.0)["ref"], None)
        for bad in ({"kind": "rumour"}, {"kind": "file", "ref": None}, {"label": ""}, {"verdict": "Probably"}):
            with self.assertRaises(RoomError, msg=str(bad)):
                new_evidence(**{"kind": "note", "label": "x", "added_by": "x", "at": 1.0, **bad})
        f = new_finding(agent="ownership-tracer", text="Four entities share a beneficial owner.", verdict="Evidence found", at=2.0)
        self.assertEqual(f["agent"], "ownership-tracer")
        with self.assertRaises(RoomError):
            new_finding(agent="", text="x", at=1.0)


class DecisionTest(unittest.TestCase):
    def test_one_decision_closes_a_room_without_a_second_approver(self):
        r = room()
        self.assertEqual(waiting_for(r), ["anyone who may decide"])
        decide(r, by="marcus@example.org", option_id_="request-more-evidence", rationale="The board minutes are unverified.", at=200.0)
        self.assertEqual((r["status"], r["closed_at"], waiting_for(r)), ("decided", 200.0, []))
        self.assertEqual(outcome(r), {"option": {"id": "request-more-evidence", "label": "Request more evidence"},
                                      "agreed": True, "decisions": 1})

    def test_a_second_approver_keeps_it_open_until_they_decide(self):
        r = room(second_approver="Marcus@Example.org ")
        self.assertEqual(r["second_approver"], "marcus@example.org")
        decide(r, by="lena@example.org", option_id_="file-sar-draft-for-signature", rationale="Layering is consistent.", at=1.0)
        self.assertEqual((r["status"], waiting_for(r), is_complete(r)), ("open", ["marcus@example.org"], False))
        self.assertEqual(may_decide(r, email="lena@example.org", role="approver"), (False, "You have decided; the room keeps your rationale."))
        self.assertEqual(may_decide(r, email="someone@example.org", role="approver")[1], "Waiting for the second approver (marcus@example.org).")
        self.assertTrue(may_decide(r, email="marcus@example.org", role="approver")[0])
        decide(r, by="marcus@example.org", option_id_="request-more-evidence", rationale="I want the minutes checked first.", at=2.0)
        self.assertEqual(r["status"], "decided")
        self.assertEqual(outcome(r)["agreed"], False)  # the two chose differently, and the room says so

    def test_a_decision_is_append_only_and_needs_a_rationale(self):
        r = room()
        decide(r, by="marcus@example.org", option_id_="close-as-legitimate-transfer", rationale="Declared dividend, documented.", at=1.0)
        with self.assertRaises(RoomError):  # the room is closed
            decide(r, by="lena@example.org", option_id_="request-more-evidence", rationale="Not so fast, please.", at=2.0)
        r2 = room(second_approver="lena@example.org")
        decide(r2, by="marcus@example.org", option_id_="request-more-evidence", rationale="Minutes first, please.", at=1.0)
        for bad in ({"by": "marcus@example.org"}, {"option_id_": "invent-an-option"}, {"rationale": "short"}):
            with self.assertRaises(RoomError, msg=str(bad)):
                decide(r2, **{"by": "lena@example.org", "option_id_": "request-more-evidence",
                              "rationale": "A proper rationale.", "at": 3.0, **bad})

    def test_who_may_decide_and_what_a_row_shows(self):
        r = room()
        self.assertEqual(may_decide(r, email="sam@example.org", role="operator"),
                         (False, "Decisions are recorded by an Admin or an Approver."))
        row = room_row(r, email="marcus@example.org", role="approver")
        self.assertEqual((row["may_decide"], row["mine"], row["evidence"], row["outcome"]), (True, False, 0, None))
        decide(r, by="marcus@example.org", option_id_="request-more-evidence", rationale="The minutes are unverified.", at=2.0)
        row = room_row(r, email="marcus@example.org", role="approver")
        self.assertEqual((row["status"], row["mine"], row["decisions"]), ("decided", True, 1))
        self.assertEqual(may_decide(r, email="x@example.org", role="admin"), (False, "This room is decided."))

    def test_zones_decide_which_rooms_exist_for_a_person(self):
        rooms = [room(), room(room_id="room_2", zone="hr")]
        self.assertEqual([r["id"] for r in visible(rooms, ["case-files"])], ["room_1"])
        self.assertEqual(visible(rooms, []), [])



class BasisTest(unittest.TestCase):
    """What each piece of evidence and each finding rests on, and who may claim what (Use Case 2)."""

    def test_defaults_say_what_a_kind_of_item_usually_is(self):
        mk = lambda kind, **kw: new_evidence(kind=kind, label="x", ref="r", added_by="p", at=1.0, **kw)["basis"]
        self.assertEqual((mk("file"), mk("output"), mk("note"), mk("claim")), ("source", "interpretation", "judgment", None))
        self.assertEqual(mk("file", basis="analytical"), "analytical")  # a SAS report attached as a file
        self.assertEqual(mk("note", basis="assumption"), "assumption")
        for kind, bad in (("note", "analytical"), ("output", "judgment"), ("claim", "source"), ("file", "whatever")):
            with self.assertRaises(RoomError):
                mk(kind, basis=bad)

    def test_agents_never_judge_and_analytical_findings_name_their_tool(self):
        self.assertEqual(new_finding(agent="a", text="t", at=1.0)["basis"], "interpretation")
        with self.assertRaisesRegex(RoomError, "people do"):
            new_finding(agent="a", text="t", at=1.0, basis="judgment")
        with self.assertRaisesRegex(RoomError, "names the tool"):
            new_finding(agent="a", text="t", at=1.0, basis="analytical")
        f = new_finding(agent="a", text="PD 4.2%", at=1.0, basis="analytical", tool="sas-viya.run_model")
        self.assertEqual((f["basis"], f["tool"]), ("analytical", "sas-viya.run_model"))

    def test_the_tool_is_checked_against_what_the_run_called(self):
        run = {"id": "tr_1", "tool_calls": [{"name": "mcp_sas_viya__run_model"}]}
        self.assertEqual(check_tool("sas-viya.run_model", run=run)["verdict"], "Evidence found")
        self.assertEqual(check_tool("sas-viya.score", run=run)["verdict"], "No evidence")
        self.assertEqual(check_tool("sas-viya.run_model", run={"id": "tr_2", "tool_calls": None})["verdict"], "Not verifiable")
        events = [{"kind": "tool.pre", "tool": "mcp_sas_viya__export", "decision": "block"},
                  {"kind": "tool.post", "tool": "mcp_sas_viya__run_model"}]
        self.assertEqual(check_tool("sas-viya.run_model", events=events)["verdict"], "Evidence found")
        self.assertEqual(check_tool("sas-viya.export", events=events)["verdict"], "Policy blocked")
        self.assertEqual(check_tool("sas-viya.run_model")["verdict"], "Not verifiable")  # nothing to check: said, not guessed


if __name__ == "__main__":
    unittest.main()
