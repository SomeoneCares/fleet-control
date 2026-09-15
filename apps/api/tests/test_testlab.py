"""Test Lab judging: checks against the run's transcript, and the claims with their four verdicts."""

import unittest

from fleetcontrol_api.testlab import VERDICTS, evaluate, mentioned_tools, output_json, tool_matches

SCREENER = {"id": "sanctions-screener", "soul": {"objective": "Screen.", "output_contract": {"format": "json", "required": ["match_count", "matches"]}}}
TEST = {"id": "sanctions-evidence", "target": "sanctions-screener", "scenario": "Screen Zephyr Maritime Ltd.",
        "required_tools": ["opensanctions.search"], "forbidden_tools": ["web.fetch"], "expected_artifact": "screening-result.json",
        "evaluator": "schema", "limits": {"max_seconds": 60, "max_tokens": 20000, "max_cost_usd": 0.1}}
CALLS = [{"name": "mcp_opensanctions__search", "arguments": '{"q": "Zephyr"}', "result": "0 matches", "answered": True},
         {"name": "write_file", "arguments": '{"path": "screening-result.json"}', "result": "ok", "answered": True}]


def run(**kw):
    base = {"status": "completed", "output": '```json\n{"match_count": 0, "matches": []}\n```', "usage": {"total_tokens": 900},
            "duration_s": 12.0, "tool_calls": CALLS, "evidence": "transcript"}
    base.update(kw)
    return base


def by_id(result):
    return {c["id"]: c["outcome"] for c in result["checks"]}


class MatchingTest(unittest.TestCase):
    def test_hermes_tool_names(self):
        self.assertTrue(tool_matches("opensanctions.search", "mcp_opensanctions__search"))
        self.assertTrue(tool_matches("web.fetch", "web_fetch"))
        self.assertTrue(tool_matches("write_file", "write_file"))
        self.assertFalse(tool_matches("search", "mcp_opensanctions__search"))  # a bare tool name is too loose
        self.assertFalse(tool_matches("web.fetch", "web_search"))

    def test_output_json_and_mentions(self):
        self.assertEqual(output_json('Result: {"a": 1} done'), {"a": 1})
        self.assertIsNone(output_json("no json here"))
        self.assertEqual(mentioned_tools("I queried opensanctions.search, e.g. twice, and wrote screening-result.json"),
                         ["opensanctions.search"])


class EvaluateTest(unittest.TestCase):
    def test_a_run_that_did_what_the_test_asks_passes(self):
        r = evaluate(TEST, SCREENER, run())
        self.assertEqual(r["status"], "passed", r["checks"])
        self.assertEqual(by_id(r), {"required:opensanctions.search": "pass", "forbidden:web.fetch": "pass", "artifact": "pass",
                                    "output": "pass", "limit:time": "pass", "limit:tokens": "pass"})
        self.assertTrue(all(c["verdict"] == "Evidence found" for c in r["claims"]))
        self.assertTrue(all(c["verdict"] in VERDICTS for c in r["claims"]))

    def test_the_agent_claims_a_screening_that_did_not_run(self):
        r = evaluate(TEST, SCREENER, run(tool_calls=[{"name": "web_fetch", "arguments": "{}", "result": "", "answered": True}],
                                         output="Screened 14 counterparties with opensanctions.search: no matches."))
        self.assertEqual(r["status"], "failed")
        outcomes = by_id(r)
        self.assertEqual((outcomes["required:opensanctions.search"], outcomes["forbidden:web.fetch"], outcomes["artifact"], outcomes["output"]),
                         ("fail", "fail", "fail", "fail"))
        verdicts = {c["claim"]: c["verdict"] for c in r["claims"]}
        self.assertEqual(verdicts["Called opensanctions.search"], "No evidence")

    def test_without_a_transcript_nothing_is_guessed(self):
        r = evaluate(TEST, SCREENER, run(tool_calls=None, evidence="none", evidence_error="GET … -> 404"))
        self.assertEqual(r["status"], "not_verifiable")
        self.assertEqual(by_id(r)["required:opensanctions.search"], "not_verifiable")
        self.assertIn("404", r["checks"][0]["detail"])
        self.assertEqual({c["verdict"] for c in r["claims"] if c["check"] in ("required:opensanctions.search", "forbidden:web.fetch")},
                         {"Not verifiable"})

    def test_a_blocked_call_is_policy_blocked(self):
        r = evaluate(TEST, SCREENER, run(blocked_tools=["web_fetch"]))
        claim = next(c for c in r["claims"] if c["check"] == "forbidden:web.fetch")
        self.assertEqual((claim["verdict"], by_id(r)["forbidden:web.fetch"]), ("Policy blocked", "pass"))

    def test_a_run_that_did_not_complete_is_an_error_and_limits_count(self):
        self.assertEqual(evaluate(TEST, SCREENER, {"status": "timeout", "error": "no result after 300 s"})["status"], "error")
        r = evaluate(TEST, SCREENER, run(duration_s=95.0, usage={"total_tokens": 25000}))
        self.assertEqual((by_id(r)["limit:time"], by_id(r)["limit:tokens"], r["status"]), ("fail", "fail", "failed"))

    def test_contains_and_exact_evaluators(self):
        t = {**TEST, "required_tools": [], "forbidden_tools": [], "expected_artifact": None, "evaluator": "contains", "expected": "unsupported"}
        self.assertEqual(evaluate(t, None, run(output="That claim is unsupported."))["status"], "passed")
        self.assertEqual(evaluate({**t, "evaluator": "exact", "expected": "ok"}, None, run(output=" ok "))["status"], "passed")
        self.assertEqual(evaluate({**t, "evaluator": "exact", "expected": "ok"}, None, run(output="okay"))["status"], "failed")


if __name__ == "__main__":
    unittest.main()
