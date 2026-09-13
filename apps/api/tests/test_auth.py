import time
import unittest

from fleetcontrol_api.auth import (
    ADMIN_PORTAL, PERMISSIONS, ROLES, allowed, check_password_policy, hash_password, new_password, permissions_for,
    token_key, verify_password,
)
from fleetcontrol_api.store import Store


class PasswordTest(unittest.TestCase):
    def test_hash_and_verify(self):
        h = hash_password("correct horse battery")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(verify_password("correct horse battery", h))
        self.assertFalse(verify_password("correct horse batterY", h))
        self.assertNotEqual(h, hash_password("correct horse battery"))  # salted

    def test_garbage_hash_never_verifies(self):
        for bad in ("", "plain", "md5$abc", "scrypt$x$y$z$a$b"):
            self.assertFalse(verify_password("anything", bad))

    def test_policy_and_generated_passwords(self):
        with self.assertRaises(ValueError):
            check_password_policy("short")
        check_password_policy(new_password())
        self.assertNotEqual(new_password(), new_password())


class RoleTest(unittest.TestCase):
    def test_five_roles_and_portals(self):
        self.assertEqual(set(ROLES), {"admin", "fleet_architect", "operator", "approver", "viewer"})
        self.assertEqual(ADMIN_PORTAL, {"admin", "fleet_architect", "operator"})

    def test_rules_agreed_with_basem(self):
        # production approvals: admin and approver only; designers cannot approve their own production changes
        self.assertEqual(PERMISSIONS["plans.approve.production"], {"admin", "approver"})
        self.assertFalse(allowed("fleet_architect", "plans.approve.production"))
        # staging approvals: operators, and admins and architects too
        self.assertEqual(PERMISSIONS["plans.approve.nonprod"], {"admin", "fleet_architect", "operator"})
        self.assertFalse(allowed("fleet_architect", "plans.apply.production"))
        self.assertTrue(allowed("approver", "plans.read"))
        self.assertFalse(allowed("approver", "instances.read"))
        self.assertTrue(allowed("viewer", "audit.read"))
        self.assertFalse(allowed("viewer", "blueprints.write"))
        self.assertEqual(permissions_for("admin"), sorted(PERMISSIONS))

    def test_every_permission_names_known_roles(self):
        for perm, roles in PERMISSIONS.items():
            self.assertTrue(roles <= set(ROLES), perm)


class SessionStoreTest(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.s.add_user("dana@x", "Dana", "fleet_architect", hash_password("correct horse battery"))

    def test_session_lifecycle(self):
        key = token_key("tok")
        self.s.create_session("dana@x", "tok", key, 60)
        self.assertEqual(self.s.session_user(key)["email"], "dana@x")
        self.s.users["dana@x"]["disabled"] = True
        self.assertIsNone(self.s.session_user(key))  # disabled users lose their sessions
        self.assertNotIn(key, self.s.sessions)

    def test_expired_session(self):
        key = token_key("old")
        self.s.create_session("dana@x", "old", key, 60)
        self.s.sessions[key]["expires"] = time.time() - 1
        self.assertIsNone(self.s.session_user(key))

    def test_failures_window(self):
        for _ in range(3):
            self.s.login_failed("dana@x")
        self.assertEqual(self.s.recent_failures("dana@x", 60), 3)
        self.s.login_failures["dana@x"] = [time.time() - 3600]
        self.assertEqual(self.s.recent_failures("dana@x", 60), 0)


if __name__ == "__main__":
    unittest.main()
