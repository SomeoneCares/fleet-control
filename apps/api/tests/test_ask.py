"""Ask the fleet: which sources an answer may rest on, what the answer cites, and how far it can be trusted."""

import unittest

from fleetcontrol_api.ask import (
    AskError, check_question, classification_of, granted_zones, grounding, instructions, parse_answer, request_text,
    room_text, save_targets, select_sources, terms,
)


def cand(key, label, text, zone="case-files", classification="confidential"):
    kind, ref = key.split(":")
    return {"key": key, "kind": kind, "ref": ref, "label": label, "zone": zone, "classification": classification, "text": text}


CANDIDATES = [
    cand("file:f1", "Wire legs AML-2026-0412", "Two of the three wire legs settle through a Limassol correspondent in Cyprus."),
    cand("file:f2", "Ownership graph 0409", "Solstice Holdings owns a Cyprus-registered intermediate entity."),
    cand("output:o1", "Weekly KPI", "Throughput was steady; no incidents."),
]


class SelectTest(unittest.TestCase):
    def test_terms_keep_case_ids_and_drop_small_words(self):
        self.assertEqual(terms("Which open cases touch Cyprus in AML-2026-0412?"),
                         ["open", "cases", "touch", "cyprus", "aml-2026-0412", "aml", "2026", "0412"])

    def test_only_matching_sources_are_sent_numbered_in_order_of_relevance(self):
        sources = select_sources("Which open cases touch Cyprus?", CANDIDATES)
        self.assertEqual([s["id"] for s in sources], ["S1", "S2"])
        self.assertEqual({s["key"] for s in sources}, {"file:f1", "file:f2"})  # "Cyprus-registered" matches "Cyprus"
        self.assertNotIn("output:o1", [s["key"] for s in sources])
        self.assertEqual(select_sources("quarterly revenue forecast", CANDIDATES), [])

    def test_a_follow_up_keeps_what_was_cited_before(self):
        sources = select_sources("Draft a summary I can paste into the weekly report", CANDIDATES, carry=["file:f2"])
        self.assertEqual(sources[0]["key"], "file:f2")

    def test_long_texts_are_cut_to_the_part_that_matches_and_the_budget_holds(self):
        filler = "\n\n".join(f"Paragraph {i} about nothing in particular." for i in range(400))
        long = cand("file:big", "Big file", filler + "\n\nThe Limassol correspondent settles in Cyprus.\n\n" + filler)
        s = select_sources("Limassol Cyprus", [long])[0]
        self.assertIn("Limassol", s["excerpt"])
        self.assertLessEqual(len(s["excerpt"]), 3000)
        self.assertTrue(s["truncated"])
        many = [cand(f"file:{i}", f"Cyprus note {i}", "Cyprus " * 900) for i in range(20)]
        chosen = select_sources("Cyprus", many)
        self.assertLessEqual(len(chosen), 8)
        self.assertLessEqual(sum(len(x["excerpt"]) for x in chosen), 24_000)


class AnswerTest(unittest.TestCase):
    def test_json_answers_are_read_and_invented_citations_dropped(self):
        a = parse_answer('```json\n{"answer": "Two cases [S1][S2].", "sources": ["S1", "S2", "S9"]}\n```', ["S1", "S2"])
        self.assertEqual((a["cited"], a["dropped"], a["format"]), (["S1", "S2"], ["S9"], "json"))

    def test_a_plain_text_answer_is_used_and_says_so(self):
        a = parse_answer("Only AML-2026-0412 does [S2].", ["S1", "S2"])
        self.assertEqual((a["answer"], a["cited"], a["format"]), ("Only AML-2026-0412 does [S2].", ["S2"], "text"))
        with self.assertRaises(AskError):
            parse_answer("   ", ["S1"])

    def test_grounding_uses_the_four_verdicts_and_never_assumes(self):
        self.assertEqual(grounding([], [])["verdict"], "No evidence")
        self.assertEqual(grounding(["S1"], [])["verdict"], "Evidence found")
        self.assertEqual(grounding(["S1"], None)["verdict"], "Not verifiable")  # no transcript: unknown, said so
        g = grounding(["S1"], [{"name": "web_search"}])
        self.assertEqual(g["verdict"], "Not verifiable")
        self.assertIn("web_search", g["detail"])

    def test_instructions_carry_only_the_sources_and_the_contract(self):
        text = instructions(asker="Dana", role="Approver", sources=select_sources("Cyprus", CANDIDATES))
        self.assertIn("[S1]", text)
        self.assertNotIn("Throughput", text)
        self.assertIn('"sources"', text)
        self.assertIn("no sources", instructions(asker="Dana", role="Approver", sources=[]))

    def test_follow_ups_carry_the_last_answers(self):
        history = [{"question": "Which cases touch Cyprus?", "answer": "Two [S1].", "status": "answered"},
                   {"question": "boom", "status": "failed"}]
        text = request_text("Summarise that", history)
        self.assertIn("Which cases touch Cyprus?", text)
        self.assertNotIn("boom", text)
        self.assertTrue(text.endswith("Question: Summarise that"))


class AccessTest(unittest.TestCase):
    ZONES = [{"id": "case-files", "read_roles": ["approver"]}, {"id": "board", "read_roles": []},
             {"id": "everyone", "read_roles": ["approver", "viewer", "operator"]}]

    def test_an_answer_is_saved_only_where_its_readers_could_read_every_source(self):
        self.assertEqual(save_targets(self.ZONES, mine=["case-files", "board", "everyone"], cited_zones=["case-files"]),
                         ["board", "case-files"])  # "everyone" would show case files to viewers and operators
        self.assertEqual(save_targets(self.ZONES, mine=["case-files", "everyone"], cited_zones=[]), ["case-files", "everyone"])

    def test_the_strictest_classification_travels(self):
        self.assertEqual(classification_of(["internal", "restricted", None]), "restricted")
        self.assertEqual(classification_of([]), "internal")

    def test_the_orchestrator_reads_only_zones_a_blueprint_grants_its_profile(self):
        bps = [{"agents": [{"id": "case-orchestrator", "content_zones": ["case-files", "shared-drafts"]},
                           {"id": "x", "hermes_profile": "orch", "content_zones": ["board"]}]}]
        self.assertEqual(granted_zones("case-orchestrator", bps), ["case-files", "shared-drafts"])
        self.assertEqual(granted_zones("orch", bps), ["board"])
        self.assertEqual(granted_zones("nobody", bps), [])

    def test_a_room_reads_as_text(self):
        room = {"question": "File a SAR?", "case": "AML-1", "status": "open", "options": [{"id": "file", "label": "File"}],
                "evidence": [{"kind": "note", "label": "Minutes", "basis": "judgment"}],
                "findings": [{"agent": "tracer", "text": "Cyprus owner", "basis": "analytical"}],
                "decisions": [{"by": "a@x", "option": "file", "rationale": "clear pattern"}]}
        text = room_text(room)
        for part in ("File a SAR?", "AML-1", "Minutes", "Cyprus owner", "File — clear pattern"):
            self.assertIn(part, text)

    def test_questions_have_a_length(self):
        self.assertEqual(check_question("  Which cases?  "), "Which cases?")
        for bad in ("", "no", "x" * 1001):
            with self.assertRaises(AskError):
                check_question(bad)


if __name__ == "__main__":
    unittest.main()
