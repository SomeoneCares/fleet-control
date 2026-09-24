"""Notifications: what each person is offered, their own address, and when an event reaches them."""

import unittest

from fleetcontrol_api.notifications import (
    NotificationError, check_address, defaults, effective, offered, reach, update,
)


class OfferTest(unittest.TestCase):
    def test_only_events_a_role_could_act_on_are_offered(self):
        self.assertIn("room.waiting", offered("approver"))
        self.assertIn("plan.approval", offered("approver"))
        self.assertNotIn("drift.detected", offered("approver"))  # Approvers do not read drift
        self.assertNotIn("room.waiting", offered("operator"))   # Operators do not decide
        self.assertEqual(offered("viewer"), [])
        self.assertEqual(defaults("approver")["events"]["room.waiting"], True)
        self.assertEqual(defaults("admin")["events"]["drift.detected"], False)

    def test_a_role_change_drops_what_is_no_longer_offered(self):
        prefs = update(defaults("admin"), "admin", events={"drift.detected": True})
        self.assertNotIn("drift.detected", effective(prefs, "approver")["events"])


class AddressTest(unittest.TestCase):
    def test_addresses_are_checked_per_platform(self):
        self.assertEqual(check_address("telegram", " 123456789 "), "123456789")
        self.assertEqual(check_address("email", "dana@example.com"), "dana@example.com")
        for platform, bad in (("telegram", "@dana"), ("telegram", ""), ("email", "dana"), ("slack", "{chat_id}")):
            with self.assertRaises(NotificationError):
                check_address(platform, bad)

    def test_a_person_is_reached_only_when_they_chose_the_event_and_gave_an_address(self):
        prefs = defaults("approver")
        self.assertIsNone(reach(prefs, "room.waiting"))  # no way to reach them yet
        with self.assertRaises(NotificationError):
            update(prefs, "approver", address="123")  # an address needs a platform first
        prefs = update(prefs, "approver", via="telegram", address="123456789")
        self.assertEqual(reach(prefs, "room.waiting"), ("telegram", "123456789"))
        self.assertIsNone(reach(prefs, "ask.answered"))  # off by default
        prefs = update(prefs, "approver", events={"room.waiting": False, "ask.answered": True})
        self.assertIsNone(reach(prefs, "room.waiting"))
        self.assertEqual(reach(prefs, "ask.answered"), ("telegram", "123456789"))
        with self.assertRaises(NotificationError):
            update(prefs, "approver", events={"drift.detected": True})  # not offered to an Approver
        self.assertEqual(update(prefs, "approver", clear=True), defaults("approver"))


if __name__ == "__main__":
    unittest.main()
