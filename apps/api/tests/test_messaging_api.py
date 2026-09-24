"""Messaging through FastAPI: channels, routes, rules from the blueprint, and deliveries, with the agent played by the test."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
READY = lambda route: {"ok": True, "platforms": [{"id": "telegram", "name": "Telegram", "configured": True, "enabled": True,
                                                  "state": "connected", "gateway_running": True}],
                       "webhooks": {"enabled": True, "base_url": "http://localhost:8644",
                                    "routes": [{"name": route, "deliver": "telegram", "deliver_only": True, "signable": True}]}}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class MessagingApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        tag = os.urandom(3).hex()
        self.tag = tag
        self.inst = f"msg-{tag}"
        pair = self.admin.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.admin.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                              headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.zone = f"msg-zone-{tag}"
        self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": self.zone, "read_roles": ["approver"]})
        self.addCleanup(lambda: store.delete_zone(self.zone))

    def agent_does(self, kind, result):
        job = self.admin.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], kind, job)
        self.assertEqual(self.admin.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent).status_code, 200)
        return job

    def channel(self, **extra):
        name = f"ops {self.tag}"
        r = self.admin.post("/api/v1/messaging/channels", json={"name": name, "instance_id": self.inst, "platform": "telegram",
                                                               "chat_id": "-100", **extra})
        self.assertEqual(r.status_code, 201, r.text)
        ch = r.json()
        self.addCleanup(lambda: store.delete_channel(ch["id"]))
        job = self.agent_does("channel_route", {"ok": True, "route": ch["route"], "created": True})
        self.assertEqual((job["params"]["action"], job["params"]["route"], job["params"]["platform"], job["params"]["chat_id"]),
                         ("create", ch["route"], "telegram", "-100"))
        self.agent_does("messaging_discover", READY(ch["route"]))  # a route change is followed by a fresh look
        return ch

    def queued(self):
        """Jobs waiting for this instance, read from the store (the agent route would long-poll when there are none)."""
        from sqlalchemy import select
        from fleetcontrol_api.store import JOBS
        with store._tx() as c:
            return [r.kind for r in c.execute(select(JOBS.c.doc["kind"].as_string().label("kind"))
                                              .where(JOBS.c.instance_id == self.inst, JOBS.c.status == "queued"))]

    def mine(self, doc, cid):
        return next(c for c in doc["channels"] if c["id"] == cid)

    def test_a_channel_gets_its_route_and_a_test_message_goes_through_the_agent(self):
        ch = self.channel()
        doc = self.admin.get("/api/v1/messaging").json()
        self.assertEqual(self.mine(doc, ch["id"])["status"], "ready")
        self.assertEqual(next(i for i in doc["instances"] if i["instance_id"] == self.inst)["webhooks_enabled"], True)

        sent = self.admin.post(f"/api/v1/messaging/channels/{ch['id']}/test")
        self.assertEqual(sent.status_code, 201, sent.text)
        job = self.agent_does("deliver_message", {"ok": True, "http_status": 200, "status": "delivered"})
        self.assertEqual((job["params"]["route"], job["params"]["delivery_id"]), (ch["route"], sent.json()["id"]))
        self.assertIn("Test message", job["params"]["text"])
        d = next(x for x in self.admin.get(f"/api/v1/messaging/deliveries?channel={ch['id']}").json() if x["id"] == sent.json()["id"])
        self.assertEqual(d["status"], "delivered")

        self.admin.post(f"/api/v1/messaging/channels/{ch['id']}/test")
        self.agent_does("deliver_message", {"ok": False, "http_status": 502, "error": "HTTP 502: Delivery failed"})
        latest = self.admin.get(f"/api/v1/messaging/deliveries?channel={ch['id']}").json()[0]
        self.assertEqual((latest["status"], latest["error"]), ("failed", "HTTP 502: Delivery failed"))

    def test_a_blueprint_rule_delivers_an_event_once_without_its_content(self):
        ch = self.channel()
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: msg-{self.tag}")
        text = text.replace('to: "teams:aml-investigations"', f'to: "{ch["ref"]}"', 1)
        self.assertEqual(self.admin.post("/api/v1/blueprints", json={"yaml": text}).status_code, 201)
        store.set_blueprint_status(f"msg-{self.tag}", 3, "applied")
        rules = [r for r in self.admin.get("/api/v1/messaging").json()["rules"] if r["blueprint"] == f"msg-{self.tag}"]
        self.assertEqual(next(r for r in rules if r["when"] == "decision_room.opened")["channel"], ch["id"])

        room = self.admin.post("/api/v1/rooms", json={"question": "Should we file a SAR for Alpha Trading Ltd?", "zone": self.zone,
                                                     "options": ["File", "Wait"], "case": "AML-2026-0412"}).json()
        job = self.agent_does("deliver_message", {"ok": True, "status": "delivered"})
        self.assertIn("case AML-2026-0412", job["params"]["text"])
        self.assertIn(f"/rooms/{room['id']}", job["params"]["text"])
        self.assertNotIn("Alpha Trading", job["params"]["text"])  # the channel does not show titles
        self.assertEqual(self.queued(), [])

        self.admin.patch(f"/api/v1/messaging/channels/{ch['id']}", json={"show_titles": True})
        self.admin.post("/api/v1/rooms", json={"question": "Should we close case Beta Holdings?", "zone": self.zone, "options": ["Close", "Keep"]})
        self.assertIn("Beta Holdings", self.agent_does("deliver_message", {"ok": True})["params"]["text"])

        self.admin.patch(f"/api/v1/messaging/channels/{ch['id']}", json={"enabled": False})
        self.admin.post("/api/v1/rooms", json={"question": "A third question for the room?", "zone": self.zone, "options": ["A", "B"]})
        self.assertEqual(self.queued(), [])

    def test_rules_are_edited_on_drafts_only(self):
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: msgd-{self.tag}")
        self.admin.post("/api/v1/blueprints", json={"yaml": text})
        url = f"/api/v1/blueprints/msgd-{self.tag}/3/delivery"
        r = self.admin.put(url, json={"rules": [{"when": "drift.detected", "to": "telegram:ops", "template": "alert"}]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["delivery"], [{"when": "drift.detected", "to": "telegram:ops", "template": "alert", "enabled": True}])
        self.assertEqual(self.admin.put(url, json={"rules": [{"when": "someday", "to": "x:y", "template": "alert"}]}).status_code, 422)
        store.set_blueprint_status(f"msgd-{self.tag}", 3, "applied")
        self.assertEqual(self.admin.put(url, json={"rules": []}).status_code, 409)

    def test_who_may_do_what_and_the_webhook_platform(self):
        self.assertEqual(signed_in("operator").get("/api/v1/messaging").status_code, 200)
        self.assertEqual(signed_in("viewer").get("/api/v1/messaging").status_code, 403)
        self.assertEqual(signed_in("operator").post("/api/v1/messaging/channels", json={"name": "x ops", "instance_id": self.inst,
                                                                                         "platform": "telegram"}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/messaging/enable-webhooks", json={"instance_id": self.inst}).status_code, 200)
        self.agent_does("webhooks_enable", {"ok": True, "enabled": True})
        self.agent_does("messaging_discover", READY("fc-none"))
        ch = self.channel()
        self.assertEqual(self.admin.post("/api/v1/messaging/channels", json={"name": f"ops {self.tag}", "instance_id": self.inst,
                                                                             "platform": "telegram"}).status_code, 409)
        self.assertEqual(self.admin.delete(f"/api/v1/messaging/channels/{ch['id']}").status_code, 200)
        self.assertEqual(self.agent_does("channel_route", {"ok": True})["params"]["action"], "remove")

    def test_messaging_can_be_switched_off_and_then_sends_nothing(self):
        ch = self.channel()
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: msgoff-{self.tag}")
        self.admin.post("/api/v1/blueprints", json={"yaml": text.replace('to: "teams:aml-investigations"', f'to: "{ch["ref"]}"', 1)})
        store.set_blueprint_status(f"msgoff-{self.tag}", 3, "applied")
        self.addCleanup(lambda: store.set_settings({"messaging_enabled": True}, "tests"))
        self.assertEqual(self.admin.patch("/api/v1/settings", json={"messaging_enabled": False}).status_code, 200)
        self.assertEqual(self.admin.get("/api/v1/auth/me").json()["features"], {"messaging": False})  # the menu hides it
        self.assertEqual(self.admin.get("/api/v1/messaging").status_code, 409)
        self.assertEqual(self.admin.post(f"/api/v1/messaging/channels/{ch['id']}/test").status_code, 409)
        self.admin.post("/api/v1/rooms", json={"question": "Should we file a SAR for this case?", "zone": self.zone, "options": ["File", "Wait"]})
        self.assertEqual(self.queued(), [])  # the rule matched, but nothing is sent while messaging is off
        self.assertTrue(any(e["action"] == "settings.updated" and "messaging_enabled" in e["detail"] for e in self.admin.get("/api/v1/audit").json()))
        self.assertEqual(signed_in("operator").patch("/api/v1/settings", json={"messaging_enabled": True}).status_code, 403)

        self.admin.patch("/api/v1/settings", json={"messaging_enabled": True})
        self.assertEqual(self.mine(self.admin.get("/api/v1/messaging").json(), ch["id"])["id"], ch["id"])  # kept while off


if __name__ == "__main__":
    unittest.main()
