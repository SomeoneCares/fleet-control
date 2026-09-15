"""The database store: job ownership, persistence across restarts, hashed tokens, queue order, atomic transitions.

Each test opens its own in-memory SQLite database; the API tests exercise the store behind FastAPI
(and, in CI, against PostgreSQL via FLEETCONTROL_DATABASE_URL)."""

import os
import tempfile
import threading
import time
import unittest

from fleetcontrol_api.store import Store


def plan(pid, status="applying", **kw):
    return {"id": pid, "status": status, "target_instance": "lab-a", "created_at": time.time(), "approvals": [], **kw}


class JobResultOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.s = Store("sqlite://")
        self.s.create_instance("lab-a", "lab", "me", "agent")
        self.s.create_instance("lab-b", "lab", "me", "agent")
        self.job = self.s.enqueue_job("lab-a", "import_profiles", {})

    def test_owner_completes_job(self):
        out = self.s.complete_job(self.job["id"], {"ok": True, "profiles": {"p": {}}}, instance_id="lab-a")
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.s.live_state_for("lab-a"), {"p": {}})

    def test_other_instance_cannot_complete_job(self):
        out = self.s.complete_job(self.job["id"], {"ok": True, "profiles": {"planted": {}}}, instance_id="lab-b")
        self.assertIsNone(out)
        self.assertEqual(self.s.get_job(self.job["id"])["status"], "queued")
        self.assertIsNone(self.s.get_job(self.job["id"])["result"])
        self.assertIsNone(self.s.live_state_for("lab-a"))

    def test_other_instance_cannot_mark_plan_applied(self):
        self.s.save_plan(plan("plan_1"))
        job = self.s.enqueue_job("lab-a", "apply", {}, {"plan_id": "plan_1"})
        self.assertIsNone(self.s.complete_job(job["id"], {"ok": True}, instance_id="lab-b"))
        self.assertEqual(self.s.get_plan("plan_1")["status"], "applying")

    def test_successful_apply_records_applied_version(self):
        self.s.save_blueprint("aml", 3, "", {}, "me")
        self.s.save_plan(plan("plan_2", blueprint={"name": "aml", "version": 3}))
        job = self.s.enqueue_job("lab-a", "apply", {}, {"plan_id": "plan_2"})
        self.s.complete_job(job["id"], {"ok": True}, instance_id="lab-a")
        self.assertEqual(self.s.applied_for("lab-a")["version"], 3)
        self.assertEqual(self.s.get_blueprint("aml", 3)["status"], "applied")
        self.assertEqual(self.s.get_plan("plan_2")["status"], "applied")

    def test_failed_apply_records_nothing(self):
        self.s.save_blueprint("aml", 3, "", {}, "me")
        self.s.save_plan(plan("plan_3", blueprint={"name": "aml", "version": 3}))
        job = self.s.enqueue_job("lab-a", "apply", {}, {"plan_id": "plan_3"})
        self.s.complete_job(job["id"], {"ok": False, "error": "boom"}, instance_id="lab-a")
        self.assertIsNone(self.s.applied_for("lab-a"))
        self.assertEqual(self.s.get_blueprint("aml", 3)["status"], "draft")
        self.assertEqual(self.s.get_plan("plan_3")["status"], "failed")

    def test_unknown_job(self):
        self.assertIsNone(self.s.complete_job("job_nope", {"ok": True}, instance_id="lab-a"))


class PersistenceTest(unittest.TestCase):
    def test_state_survives_a_restart(self):
        url = "sqlite:///" + os.path.join(tempfile.mkdtemp(), "fc.db").replace("\\", "/")
        a = Store(url)
        a.add_user("dana@example.org", "Dana", "fleet_architect", "hash")
        inst = a.create_instance("lab-a", "lab", "dana@example.org", "agent")
        _, agent_token = a.pair(inst["pairing_token"], "0.1.0", {"surfaces": {"api": "ok"}})
        a.save_blueprint("aml", 1, "yaml text", {"k": 1}, "dana@example.org")
        a.save_plan(plan("plan_p", status="planned"))
        job = a.enqueue_job("lab-a", "import_profiles", {})
        a.record("dana@example.org", "test.action", "target")
        a.engine.dispose()

        b = Store(url)  # what a restarted API sees
        self.assertEqual(b.get_user("dana@example.org")["role"], "fleet_architect")
        self.assertEqual(b.instance_for_agent_token(agent_token), "lab-a")  # the agent stays paired
        self.assertEqual(b.get_instance("lab-a")["status"], "healthy")
        self.assertEqual(b.get_blueprint("aml", 1)["parsed"], {"k": 1})
        self.assertEqual(b.get_plan("plan_p")["status"], "planned")
        self.assertEqual(b.next_job("lab-a", timeout=0)["id"], job["id"])  # queued work is still delivered
        self.assertEqual(b.audit_log(limit=1)[0]["action"], "test.action")
        b.engine.dispose()

    def test_tokens_are_stored_only_as_hashes(self):
        s = Store("sqlite://")
        inst = s.create_instance("lab-a", "lab", "me", "agent")
        _, agent_token = s.pair(inst["pairing_token"], "0.1.0", {})
        with s.engine.connect() as c:
            dump = repr(c.exec_driver_sql("select * from tokens").fetchall()) + repr(c.exec_driver_sql("select * from instances").fetchall())
        self.assertNotIn(agent_token, dump)
        self.assertNotIn(inst["pairing_token"], dump)
        self.assertIsNone(s.pair(inst["pairing_token"], "0.1.0", {}))  # single use
        self.assertIsNone(s.instance_for_agent_token(inst["pairing_token"]))  # a pairing token is not an agent token


class QueueAndTransitionTest(unittest.TestCase):
    def setUp(self):
        self.s = Store("sqlite://")
        self.s.create_instance("a", "lab", "me", "agent")

    def test_jobs_come_out_in_order_and_once(self):
        first = self.s.enqueue_job("a", "push_policy", {})
        second = self.s.enqueue_job("a", "apply", {})
        self.assertEqual(self.s.next_job("a", timeout=0)["id"], first["id"])
        self.assertEqual(self.s.next_job("a", timeout=0)["id"], second["id"])
        self.assertIsNone(self.s.next_job("a", timeout=0))
        self.assertEqual(self.s.get_job(first["id"])["status"], "running")
        self.assertIsNone(self.s.next_job("unknown-instance", timeout=0))

    def test_long_poll_wakes_when_a_job_is_queued(self):
        got = []
        t = threading.Thread(target=lambda: got.append(self.s.next_job("a", timeout=5)))
        t.start()
        time.sleep(0.2)
        job = self.s.enqueue_job("a", "import_profiles", {})
        t.join(3)
        self.assertEqual(got and got[0]["id"], job["id"])

    def test_only_one_apply_wins(self):
        self.s.save_plan(plan("p1", status="planned"))
        self.assertTrue(self.s.transition_plan("p1", "planned", "applying"))
        self.assertFalse(self.s.transition_plan("p1", "planned", "applying"))
        self.assertEqual(self.s.get_plan("p1")["status"], "applying")

    def test_an_approval_counts_once(self):
        self.s.save_plan(plan("p2", status="planned"))
        self.s.add_approval("p2", "marcus@example.org")
        self.assertEqual(self.s.add_approval("p2", "marcus@example.org")["approvals"], ["marcus@example.org"])

    def test_user_fields_are_checked(self):
        self.s.add_user("x@example.org", "X", "viewer", "h")
        self.assertEqual(self.s.update_user("x@example.org", disabled=True)["disabled"], True)
        with self.assertRaises(ValueError):
            self.s.update_user("x@example.org", email="y@example.org")


if __name__ == "__main__":
    unittest.main()
