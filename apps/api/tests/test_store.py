import unittest

from fleetcontrol_api.store import Store


class JobResultOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.s.create_instance("lab-a", "lab", "me", "agent")
        self.s.create_instance("lab-b", "lab", "me", "agent")
        self.job = self.s.enqueue_job("lab-a", "import_profiles", {})

    def test_owner_completes_job(self):
        out = self.s.complete_job(self.job["id"], {"ok": True, "profiles": {"p": {}}}, instance_id="lab-a")
        self.assertEqual(out["status"], "done")
        self.assertEqual(self.s.live_state["lab-a"], {"p": {}})

    def test_other_instance_cannot_complete_job(self):
        out = self.s.complete_job(self.job["id"], {"ok": True, "profiles": {"planted": {}}}, instance_id="lab-b")
        self.assertIsNone(out)
        self.assertEqual(self.s.jobs[self.job["id"]]["status"], "queued")
        self.assertIsNone(self.s.jobs[self.job["id"]]["result"])
        self.assertNotIn("lab-a", self.s.live_state)

    def test_other_instance_cannot_mark_plan_applied(self):
        self.s.plans["plan_1"] = {"status": "applying"}
        job = self.s.enqueue_job("lab-a", "apply", {}, {"plan_id": "plan_1"})
        self.assertIsNone(self.s.complete_job(job["id"], {"ok": True}, instance_id="lab-b"))
        self.assertEqual(self.s.plans["plan_1"]["status"], "applying")

    def test_unknown_job(self):
        self.assertIsNone(self.s.complete_job("job_nope", {"ok": True}, instance_id="lab-a"))


if __name__ == "__main__":
    unittest.main()
