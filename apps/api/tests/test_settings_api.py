"""Settings (General, Approvals) and API tokens through FastAPI. Skipped without FastAPI/httpx."""

import os
import time
import unittest

try:
    from fastapi.testclient import TestClient
    from sqlalchemy import update

    from fleetcontrol_api.main import app, store
    from fleetcontrol_api.settings import DEFAULTS
    from fleetcontrol_api.store import API_TOKENS
    from tests.helpers import PASSWORD, signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
H = {"X-Fleet-Control": "1"}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class SettingsApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")

    def tearDown(self):
        store.set_settings(dict(DEFAULTS), "tests")  # the store is shared by every test in the process

    def _instance(self, environment):
        c = self.admin
        iid = f"set-{environment[:4]}-{os.urandom(3).hex()}"
        pair = c.post("/api/v1/instances", json={"id": iid, "environment": environment, "mode": "agent"}).json()["pairing_token"]
        tok = c.post("/agent/v1/pair", json={"instance_id": iid, "agent_version": "0.1.0", "report": {}},
                     headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        agent = {"Authorization": f"Bearer {tok}"}
        job = c.post(f"/api/v1/instances/{iid}/import").json()["job_id"]
        self.assertEqual(c.get(f"/agent/v1/instances/{iid}/jobs/next", headers=agent).json()["id"], job)
        self.assertEqual(c.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "profiles": {}}, headers=agent).status_code, 200)
        return iid

    def _plan(self, iid):
        with open(EXAMPLE, encoding="utf-8") as f:  # 409 once another test has applied v3 (immutable)
            self.assertIn(self.admin.post("/api/v1/blueprints", json={"yaml": f.read()}).status_code, (201, 409))
        r = self.admin.post("/api/v1/plans", json={"blueprint": "aml-investigation", "version": 3, "instance_id": iid})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def test_defaults_and_who_may_change_them(self):
        r = self.admin.get("/api/v1/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["values"], DEFAULTS)
        self.assertEqual(signed_in("operator").get("/api/v1/settings").status_code, 200)
        self.assertEqual(signed_in("viewer").get("/api/v1/settings").status_code, 403)
        self.assertEqual(signed_in("fleet_architect").patch("/api/v1/settings", json={"approvals_staging": 1}).status_code, 403)

    def test_values_outside_their_bounds_are_refused(self):
        for body in ({"approvals_production": 0}, {"session_hours": 48}, {"workspace_name": "  "},
                     {"token_max_days": 0}, {"approvals_staging": 9}, {"no_such_setting": 1}):
            self.assertEqual(self.admin.patch("/api/v1/settings", json=body).status_code, 422, body)

    def test_approval_floor_applies_to_new_plans(self):
        r = self.admin.patch("/api/v1/settings", json={"approvals_production": 3, "approvals_staging": 1})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["values"]["approvals_production"], 3)
        self.assertEqual(r.json()["updated"]["by"], r.json()["updated"]["by"])  # who and when are recorded
        self.assertEqual(self._plan(self._instance("production"))["approvals_required"], 3)
        self.assertEqual(self._plan(self._instance("staging"))["approvals_required"], 1)
        self.assertEqual(self._plan(self._instance("lab"))["approvals_required"], 0)
        audit = self.admin.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "settings.updated" and "approvals_production: 2 → 3" in e["detail"] for e in audit))

    def test_session_length_and_workspace_name(self):
        self.assertEqual(self.admin.patch("/api/v1/settings", json={"session_hours": 1, "workspace_name": "Meridian Bank"}).status_code, 200)
        r = TestClient(app, headers=H).post("/api/v1/auth/login", json={"email": user("viewer"), "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Max-Age=3600", r.headers["set-cookie"])
        self.assertEqual(r.json()["workspace_name"], "Meridian Bank")


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ApiTokenTest(unittest.TestCase):
    def _token(self, client, **body):
        r = client.post("/api/v1/tokens", json={"name": "ci", "expires_days": 7, **body})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    @staticmethod
    def _bearer(secret):
        return {"Authorization": f"Bearer {secret}"}

    def test_a_token_acts_as_its_owner_and_no_more(self):
        c = signed_in("operator")
        made = self._token(c)
        self.assertTrue(made["secret"].startswith("fct_"))
        listed = c.get("/api/v1/tokens").json()
        self.assertEqual([t["id"] for t in listed], [made["token"]["id"]])
        self.assertNotIn(made["secret"], str(listed))
        bare, h = TestClient(app), self._bearer(made["secret"])  # no cookie, no X-Fleet-Control header
        self.assertEqual(bare.get("/api/v1/instances", headers=h).status_code, 200)
        self.assertEqual(bare.get("/api/v1/users", headers=h).status_code, 403)  # an Operator cannot
        self.assertEqual(bare.post("/api/v1/instances/no-such-instance/import", headers=h).status_code, 404)  # a write got through
        self.assertEqual(bare.post("/api/v1/tokens", json={"name": "more"}, headers=h).status_code, 403)
        self.assertEqual(bare.post("/api/v1/auth/password", json={"current": "x", "new": "y" * 12}, headers=h).status_code, 403)
        self.assertIsNotNone(c.get("/api/v1/tokens").json()[0]["last_used_at"])
        self.assertEqual(c.delete(f"/api/v1/tokens/{made['token']['id']}").status_code, 200)
        self.assertEqual(bare.get("/api/v1/instances", headers=h).status_code, 401)

    def test_expired_disabled_and_unknown_tokens_are_refused(self):
        email = user("operator")
        c = signed_in("operator", email)
        expired, live = self._token(c), self._token(c, name="second")
        with store.engine.begin() as conn:
            conn.execute(update(API_TOKENS).where(API_TOKENS.c.id == expired["token"]["id"]).values(expires_at=time.time() - 1))
        bare = TestClient(app)
        self.assertEqual(bare.get("/api/v1/instances", headers=self._bearer(expired["secret"])).status_code, 401)
        self.assertEqual(bare.get("/api/v1/instances", headers=self._bearer(live["secret"])).status_code, 200)
        store.update_user(email, disabled=True)
        self.assertEqual(bare.get("/api/v1/instances", headers=self._bearer(live["secret"])).status_code, 401)
        self.assertEqual(bare.get("/api/v1/instances", headers=self._bearer("fct_not-a-real-token")).status_code, 401)

    def test_lifetime_limit_and_who_may_revoke(self):
        owner = signed_in("fleet_architect")
        self.assertEqual(owner.post("/api/v1/tokens", json={"name": "forever", "expires_days": 1000}).status_code, 422)
        tid = self._token(owner)["token"]["id"]
        viewer = signed_in("viewer")
        self.assertEqual(viewer.delete(f"/api/v1/tokens/{tid}").status_code, 404)
        self.assertEqual(viewer.get("/api/v1/tokens", params={"all_people": "true"}).status_code, 403)
        admin = signed_in("admin")
        self.assertIn(tid, [t["id"] for t in admin.get("/api/v1/tokens", params={"all_people": "true"}).json()])
        self.assertIsNotNone(admin.delete(f"/api/v1/tokens/{tid}").json()["revoked_at"])
        audit = admin.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "token.revoked" and e["target"] == tid for e in audit))


if __name__ == "__main__":
    unittest.main()
