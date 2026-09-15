"""Sign-in, the five roles and the approval rules, end to end through FastAPI. Skipped without FastAPI/httpx."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import app, store
    from tests.helpers import PASSWORD, signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
H = {"X-Fleet-Control": "1"}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class SignInTest(unittest.TestCase):
    def test_everything_needs_a_session(self):
        c = TestClient(app)
        for path in ("/api/v1/instances", "/api/v1/blueprints", "/api/v1/plans", "/api/v1/audit", "/api/v1/auth/me"):
            self.assertEqual(c.get(path).status_code, 401, path)
        self.assertEqual(c.post("/api/v1/instances", json={"id": "x1", "environment": "lab"}, headers=H).status_code, 401)

    def test_sign_in_me_sign_out(self):
        email = user("operator")
        c = TestClient(app, headers=H)
        self.assertEqual(c.post("/api/v1/auth/login", json={"email": email, "password": "wrong password!!"}).status_code, 401)
        r = c.post("/api/v1/auth/login", json={"email": email.upper(), "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["role"], r.json()["portal"]), ("operator", "admin"))
        self.assertIn("httponly", r.headers["set-cookie"].lower())
        self.assertEqual(c.get("/api/v1/auth/me").json()["email"], email)
        self.assertEqual(c.post("/api/v1/auth/logout").status_code, 200)
        self.assertEqual(c.get("/api/v1/auth/me").status_code, 401)

    def test_unknown_email_and_lockout(self):
        c = TestClient(app, headers=H)
        self.assertEqual(c.post("/api/v1/auth/login", json={"email": "nobody@test.local", "password": PASSWORD}).status_code, 401)
        email = user("viewer")
        for _ in range(5):
            c.post("/api/v1/auth/login", json={"email": email, "password": "not the password"})
        self.assertEqual(c.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code, 429)

    def test_writes_need_the_header(self):
        c = signed_in("admin")
        r = c.post("/api/v1/instances", json={"id": "hdr-" + os.urandom(2).hex(), "environment": "lab"}, headers={"X-Fleet-Control": ""})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(TestClient(app).post("/api/v1/auth/login", json={"email": "a@b", "password": "x"}).status_code, 403)

    def test_change_password(self):
        email = user("viewer")
        c = signed_in("viewer", email)
        self.assertEqual(c.post("/api/v1/auth/password", json={"current": "wrong one here", "new": "a new long password"}).status_code, 403)
        self.assertEqual(c.post("/api/v1/auth/password", json={"current": PASSWORD, "new": "short"}).status_code, 422)
        self.assertEqual(c.post("/api/v1/auth/password", json={"current": PASSWORD, "new": "a new long password"}).status_code, 200)
        fresh = TestClient(app, headers=H)
        self.assertEqual(fresh.post("/api/v1/auth/login", json={"email": email, "password": "a new long password"}).status_code, 200)
        store.update_user(email, password_hash=store.get_user(user("admin"))["password_hash"])  # back to the shared test password


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class RoleLimitsTest(unittest.TestCase):
    def test_what_each_role_can_reach(self):
        viewer, approver, architect, operator = (signed_in(r) for r in ("viewer", "approver", "fleet_architect", "operator"))
        self.assertEqual(viewer.get("/api/v1/audit").status_code, 200)
        self.assertEqual(viewer.get("/api/v1/blueprints").status_code, 200)
        self.assertEqual(viewer.get("/api/v1/instances").status_code, 403)
        self.assertEqual(approver.get("/api/v1/plans").status_code, 200)
        self.assertEqual(approver.get("/api/v1/instances").status_code, 403)
        self.assertEqual(approver.get("/api/v1/audit").status_code, 403)
        self.assertEqual(architect.post("/api/v1/instances", json={"id": "arch-" + os.urandom(2).hex(), "environment": "lab"}).status_code, 403)
        self.assertEqual(operator.post("/api/v1/blueprints", json={"yaml": "x"}).status_code, 403)
        self.assertEqual(operator.get("/api/v1/users").status_code, 403)
        me = viewer.get("/api/v1/auth/me").json()
        self.assertEqual(me["portal"], "workspace")
        self.assertNotIn("blueprints.write", me["permissions"])


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ApprovalRulesTest(unittest.TestCase):
    """Agreed rules: production approvals by Admin or Approver only, never by the plan's creator, one per person;
    Architects and Operators may approve staging; production applies by Admin or Operator after enough approvals."""

    def setUp(self):
        # these tests are about who approves and applies; the test-suite gate has its own tests (test_testlab_api)
        store.set_settings({"require_tests_for_production": False}, "tests")
        self.addCleanup(store.set_settings, {"require_tests_for_production": True}, "tests")
        self.admin = signed_in("admin")
        self.inst = "prod-" + os.urandom(3).hex()
        pair = self.admin.post("/api/v1/instances", json={"id": self.inst, "environment": "production", "mode": "agent"}).json()["pairing_token"]
        self.agent = {"Authorization": "Bearer " + self.admin.post(
            "/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {"surfaces": {"api": "ok"}}},
            headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]}
        with open(EXAMPLE, encoding="utf-8") as f:
            self.admin.post("/api/v1/blueprints", json={"yaml": f.read()})
        self.admin.post(f"/api/v1/instances/{self.inst}/import")
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json={"ok": True, "profiles": {}}, headers=self.agent)
        self.architect = signed_in("fleet_architect")
        self.plan = self.architect.post("/api/v1/plans", json={"blueprint": "aml-investigation", "version": 3, "instance_id": self.inst}).json()

    def _approve(self, c):
        return c.post(f"/api/v1/plans/{self.plan['id']}/approve")

    def test_production_approval_rules(self):
        self.assertEqual(self.plan["approvals_required"], 2)
        self.assertEqual(self._approve(self.architect).status_code, 403)  # role cannot approve production
        self.assertEqual(self._approve(signed_in("operator")).status_code, 403)
        approver = signed_in("approver")
        self.assertEqual(self._approve(approver).status_code, 200)
        self.assertEqual(self._approve(approver).json()["approvals"].__len__(), 1)  # one person counts once
        operator = signed_in("operator")
        self.assertEqual(operator.post(f"/api/v1/plans/{self.plan['id']}/apply").status_code, 409)  # 1 of 2
        self.assertEqual(self._approve(self.admin).status_code, 200)
        self.assertEqual(self.architect.post(f"/api/v1/plans/{self.plan['id']}/apply").status_code, 403)  # architects never apply production
        self.assertEqual(operator.post(f"/api/v1/plans/{self.plan['id']}/apply").status_code, 200)
        self.assertEqual(operator.post(f"/api/v1/plans/{self.plan['id']}/apply").status_code, 409)  # not twice
        waiting = [p["id"] for p in approver.get("/api/v1/plans", params={"status": "planned"}).json()]
        self.assertNotIn(self.plan["id"], waiting)

    def test_creator_cannot_approve_own_plan(self):
        own = self.admin.post("/api/v1/plans", json={"blueprint": "aml-investigation", "version": 3, "instance_id": self.inst}).json()
        r = self.admin.post(f"/api/v1/plans/{own['id']}/approve")
        self.assertEqual(r.status_code, 403)
        self.assertIn("another person", r.json()["detail"])


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class PeopleTest(unittest.TestCase):
    def test_invite_update_disable_reset(self):
        admin = signed_in("admin")
        email = f"new-{os.urandom(3).hex()}@test.local"
        r = admin.post("/api/v1/users", json={"email": email, "name": "New Person", "role": "approver"})
        self.assertEqual(r.status_code, 201, r.text)
        temp = r.json()["password"]
        self.assertNotIn("password_hash", r.json()["user"])
        self.assertEqual(admin.post("/api/v1/users", json={"email": email, "name": "Again", "role": "viewer"}).status_code, 409)
        person = TestClient(app, headers=H)
        self.assertEqual(person.post("/api/v1/auth/login", json={"email": email, "password": temp}).status_code, 200)
        self.assertEqual(admin.patch(f"/api/v1/users/{email}", json={"role": "viewer"}).json()["role"], "viewer")
        self.assertEqual(person.get("/api/v1/auth/me").json()["role"], "viewer")  # takes effect immediately
        self.assertEqual(admin.patch(f"/api/v1/users/{email}", json={"disabled": True}).status_code, 200)
        self.assertEqual(person.get("/api/v1/auth/me").status_code, 401)  # sessions end
        admin.patch(f"/api/v1/users/{email}", json={"disabled": False})
        new = admin.post(f"/api/v1/users/{email}/reset-password").json()["password"]
        self.assertEqual(TestClient(app, headers=H).post("/api/v1/auth/login", json={"email": email, "password": temp}).status_code, 401)
        self.assertEqual(TestClient(app, headers=H).post("/api/v1/auth/login", json={"email": email, "password": new}).status_code, 200)
        people = {p["email"]: p for p in admin.get("/api/v1/users").json()}
        self.assertIn(email, people)
        self.assertTrue(all("password_hash" not in p for p in people.values()))
        roles = {r["role"]: r for r in admin.get("/api/v1/roles").json()}
        self.assertEqual(set(roles), {"admin", "fleet_architect", "operator", "approver", "viewer"})

    def test_admins_cannot_lock_themselves_out(self):
        email = user("admin")
        admin = signed_in("admin", email)
        self.assertEqual(admin.patch(f"/api/v1/users/{email}", json={"disabled": True}).status_code, 409)
        self.assertEqual(admin.patch(f"/api/v1/users/{email}", json={"role": "viewer"}).status_code, 409)

    def test_last_active_admin_is_kept(self):
        saved = {u["email"]: u["disabled"] for u in store.list_users()}
        try:
            for u in store.list_users():
                if u["role"] == "admin":
                    store.update_user(u["email"], disabled=True)
            keeper, other = user("admin"), user("admin")
            admin = signed_in("admin", keeper)
            self.assertEqual(admin.patch(f"/api/v1/users/{other}", json={"disabled": True}).status_code, 200)
            store.update_user(other, disabled=False)
            store.update_user(keeper, disabled=True)  # now `other` is the only active admin
            boss = signed_in("admin", other)
            self.assertEqual(boss.patch(f"/api/v1/users/{keeper}", json={"role": "viewer"}).status_code, 200)  # keeper was disabled: fine
        finally:
            for e, disabled in saved.items():
                store.update_user(e, disabled=disabled)


if __name__ == "__main__":
    unittest.main()
