"""Messaging: channels, the rules that pick them, and what a message may say."""

import unittest

from fleetcontrol_api.messaging import (
    MessagingError, channel_health, channel_id, merge_gateway, new_channel, render, rules_from, test_text,
)

CH = new_channel(name="Ops on-call", platform="telegram", instance_id="lab-01", by="a@x", at=1.0, chat_id="-100")
READY = {"platforms": [{"id": "telegram", "configured": True, "state": "connected"}],
         "webhooks": {"enabled": True, "routes": [{"name": "fc-ops-on-call", "signable": True}]}}


class ChannelTest(unittest.TestCase):
    def test_a_channel_is_named_the_way_blueprints_refer_to_it(self):
        self.assertEqual((CH["id"], CH["ref"], CH["route"], CH["chat_id"]), ("ops-on-call", "telegram:ops-on-call", "fc-ops-on-call", "-100"))
        self.assertFalse(CH["show_titles"])  # case content stays out of chats unless someone decides otherwise
        with self.assertRaises(MessagingError):
            channel_id("!!")
        with self.assertRaises(MessagingError):
            new_channel(name="x ok", platform="", instance_id="i", by="a", at=1.0)

    def test_health_says_what_is_missing_and_what_to_do(self):
        self.assertEqual(channel_health(CH, READY)["status"], "ready")
        self.assertEqual(channel_health(CH, None)["status"], "unknown")
        cases = [
            ({**READY, "webhooks": {"enabled": False}}, "webhooks_off"),
            ({**READY, "platforms": []}, "platform_missing"),
            ({**READY, "platforms": [{"id": "telegram", "configured": True, "state": "error", "error_message": "bad token"}]}, "platform_down"),
            ({**READY, "webhooks": {"enabled": True, "routes": []}}, "route_missing"),
            ({**READY, "webhooks": {"enabled": True, "routes": [{"name": "fc-ops-on-call", "signable": False}]}}, "route_missing"),
        ]
        for state, want in cases:
            self.assertEqual(channel_health(CH, state)["status"], want, state)
        self.assertEqual(channel_health({**CH, "enabled": False}, READY)["status"], "disabled")
        # a failed platform is down even while the gateway process runs
        bad = {**READY, "platforms": [{"id": "telegram", "configured": True, "state": "fatal", "gateway_running": True}]}
        self.assertEqual(channel_health(CH, bad)["status"], "platform_down")

    def test_the_gateways_own_record_beats_a_stale_dashboard(self):
        stale = [{"id": "telegram", "configured": True, "state": "gateway_stopped", "gateway_running": False}]
        live = {"alive": True, "platforms": {"telegram": {"state": "connected"}}}
        merged = merge_gateway(stale, live)
        self.assertEqual((merged[0]["state"], merged[0]["dashboard_state"]), ("connected", "gateway_stopped"))
        self.assertEqual(channel_health(CH, {**READY, "platforms": merged})["status"], "ready")
        # a dead or stale record proves nothing: the dashboard's view stands
        self.assertEqual(merge_gateway(stale, {**live, "alive": False}), stale)
        self.assertEqual(merge_gateway(stale, None), stale)
        down = merge_gateway(stale, {"alive": True, "platforms": {"telegram": {"state": "fatal", "error_message": "bad token"}}})
        self.assertEqual(channel_health(CH, {**READY, "platforms": down})["detail"], "telegram is fatal: bad token")


class RuleTest(unittest.TestCase):
    def test_rules_come_from_the_blueprints_and_find_their_channel(self):
        bp = {"metadata": {"name": "aml", "version": 3}, "delivery": [
            {"when": "decision_room.opened", "to": "telegram:ops-on-call", "template": "decision-request"},
            {"when": "drift.detected", "to": "slack:#fleet-ops", "template": "alert", "enabled": False}]}
        rules = rules_from([bp], [CH])
        self.assertEqual([(r["event"], r["channel"], r["enabled"]) for r in rules],
                         [("Decision Room opened", "ops-on-call", True), ("Drift detected", None, False)])


class RenderTest(unittest.TestCase):
    CTX = {"title": "Should we file a SAR for Alpha Trading Ltd?", "case": "AML-2026-0412", "link": "/rooms/room_1"}

    def test_by_default_a_message_names_no_case_content(self):
        text = render("decision_room.opened", "decision-request", self.CTX, show_titles=False, portal_url="https://fc.example/")
        self.assertNotIn("Alpha Trading", text)
        self.assertIn("case AML-2026-0412", text)
        self.assertIn("Open: https://fc.example/rooms/room_1", text)
        self.assertIn("a reply here is not a decision", text)

    def test_a_channel_that_shows_titles_gets_the_question(self):
        text = render("decision_room.opened", "decision-request", self.CTX, show_titles=True, portal_url="http://p")
        self.assertIn("Alpha Trading", text)
        self.assertTrue(text.startswith("Fleet Control · Decision needed"))

    def test_alerts_and_tests_carry_what_happened_and_a_link(self):
        text = render("drift.detected", "alert", {"instance": "lab-01", "detail": "2 fields differ", "link": "/instances/lab-01/drift"},
                      show_titles=False, portal_url="http://p")
        self.assertEqual(text, "Fleet Control · Drift detected\non lab-01\n2 fields differ\nOpen: http://p/instances/lab-01/drift")
        self.assertIn("telegram:ops-on-call", test_text(CH, "a@x", "http://p"))
        self.assertLessEqual(len(render("apply.failed", "alert", {"detail": "x" * 5000}, show_titles=True, portal_url="http://p")), 1000)


if __name__ == "__main__":
    unittest.main()
