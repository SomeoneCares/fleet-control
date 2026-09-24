"""Personal notifications through FastAPI: a direct-message channel, a person's own address, and who is told."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import _deliver, store
    from tests.helpers import signed_in, user
except ImportError:  # pragma: no cover
    TestClient = None

READY = lambda route: {"ok": True, "platforms": [{"id": "telegram", "name": "Telegram", "configured": True, "enabled": True,
                                                  "state": "connected", "gateway_running": True}],
                       "webhooks": {"enabled": True, "routes": [{"name": route, "signable": True}]}}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class NotificationsApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        tag = self.tag = os.urandom(3).hex()
        self.inst = f"ntf-{tag}"
        pair = self.admin.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.zone = f"ntf-zone-{tag}"
        self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": self.zone, "read_roles": ["approver"]})
        self.addCleanup(lambda: store.delete_zone(self.zone))
        # the direct-message channel: every other test's channels are theirs; this one must be the only telegram DM
        for ch in store.list_channels():
            if ch.get("direct"):
                store.delete_channel(ch["id"])
        r = self.admin.post("/api/v1/messaging/channels", json={"name": f"dm {tag}", "instance_id": self.inst, "platform": "telegram", "direct": True})
        self.assertEqual(r.status_code, 201, r.text)
        self.ch = r.json()
        self.addCleanup(lambda: store.delete_channel(self.ch["id"]))
        job = self.agent_does("channel_route", {"ok": True})
        self.assertEqual(job["params"]["chat_id"], "{chat_id}")
        self.agent_does("messaging_discover", READY(self.ch["route"]))
        self.lena_email = user("approver")
        self.lena = signed_in("approver", self.lena_email)

    def agent_does(self, kind, result):
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], kind, job)
        self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent)
        return job

    def queued(self):
        from sqlalchemy import select
        from fleetcontrol_api.store import JOBS
        with store._tx() as c:
            return [r.doc for r in c.execute(select(JOBS.c.doc).where(JOBS.c.instance_id == self.inst, JOBS.c.status == "queued"))]

    def test_a_person_sets_their_own_address_and_is_told_what_they_chose(self):
        doc = self.lena.get("/api/v1/me/notifications").json()
        self.assertEqual([p["platform"] for p in doc["platforms"]], ["telegram"])
        self.assertIn("room.waiting", [e["event"] for e in doc["events"]])
        self.assertEqual(self.lena.patch("/api/v1/me/notifications", json={"via": "telegram", "address": "@lena"}).status_code, 422)
        self.assertEqual(self.lena.patch("/api/v1/me/notifications", json={"via": "slack"}).status_code, 422)  # no DM channel for it
        r = self.lena.patch("/api/v1/me/notifications", json={"via": "telegram", "address": "123456789"})
        self.assertEqual(r.status_code, 200, r.text)
        audit = self.admin.get("/api/v1/audit").json()
        self.assertFalse(any("123456789" in (e["detail"] + e["target"]) for e in audit))  # the address is never logged

        self.admin.post("/api/v1/rooms", json={"question": "Should we file a SAR for Alpha Trading?", "zone": self.zone,
                                               "options": ["File", "Wait"], "case": "AML-1"})
        mine = [j for j in self.queued() if j["params"].get("chat_id") == "123456789"]
        self.assertEqual(len(mine), 1)
        params = mine[0]["params"]
        self.assertTrue(params["direct"])
        self.assertIn("Decision waiting for you", params["text"])
        self.assertNotIn("Alpha Trading", params["text"])  # titles only if she asks for them
        d = next(x for x in store.list_deliveries(channel=self.ch["id"]) if x["id"] == params["delivery_id"])
        self.assertEqual(d["to"], self.lena_email)
        self.assertNotIn("123456789", str(d))  # deliveries record the account, not the address

    def test_nobody_is_told_about_what_they_cannot_see(self):
        op = signed_in("operator")  # Operators neither read this zone nor decide
        self.assertNotIn("room.waiting", [e["event"] for e in op.get("/api/v1/me/notifications").json()["events"]])
        self.lena.patch("/api/v1/me/notifications", json={"via": "telegram", "address": "123456789", "events": {"room.waiting": False}})
        self.admin.post("/api/v1/rooms", json={"question": "Another question for the room?", "zone": self.zone, "options": ["A", "B"]})
        self.assertFalse([j for j in self.queued() if j["params"].get("chat_id") == "123456789"])

    def test_a_direct_channel_never_sends_without_an_address_and_rules_cannot_use_it(self):
        with self.assertRaises(ValueError):
            _deliver(self.ch, event="test", key="k-empty", text="x", chat_id="  ")
        self.assertNotIn(self.ch["id"], [r["channel"] for r in self.admin.get("/api/v1/messaging").json()["rules"]])
        self.assertEqual(self.admin.post(f"/api/v1/messaging/channels/{self.ch['id']}/test").status_code, 409)  # admin has no address
        self.assertEqual(self.lena.post("/api/v1/me/notifications/test").status_code, 409)
        self.lena.patch("/api/v1/me/notifications", json={"via": "telegram", "address": "987654321"})
        self.assertEqual(self.lena.post("/api/v1/me/notifications/test").status_code, 201)
        self.assertEqual(self.agent_does("deliver_message", {"ok": True})["params"]["chat_id"], "987654321")
        self.assertEqual(self.admin.post("/api/v1/messaging/channels", json={
            "name": f"dm2 {self.tag}", "instance_id": self.inst, "platform": "telegram", "direct": True, "chat_id": "-100"}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
