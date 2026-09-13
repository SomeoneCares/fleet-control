"""Unit tests for the daemon pieces that don't need a Hermes host."""
import json, os, socket, tempfile, threading, time, unittest, queue

from fleetctl_agent.hermes_local import diff_managed, ROUTES, API_ROUTES
from fleetctl_agent.daemon import Jobs, AgentConfig, PluginSocketServer


class FakeHermes:
    def __init__(self):
        self.cfg = type("C", (), {"hermes_home": tempfile.mkdtemp()})()
        self.calls = []
    def live_profile_state(self, name):
        if name == "missing":
            raise RuntimeError("404")
        return {"description": "r", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:a", "skills": ["x"], "toolsets": [], "mcps": []}
    def snapshot(self, d):
        os.makedirs(d, exist_ok=True); return os.path.join(d, "snap.tar.gz")
    def ensure_profile(self, *a, **k): self.calls.append(("ensure_profile", a))
    def write_soul(self, *a, **k): self.calls.append(("write_soul", a))
    def set_skill(self, *a, **k):
        if a[1] == "boom": raise RuntimeError("skill install failed")
        self.calls.append(("set_skill", a))
    def set_toolset(self, *a, **k): self.calls.append(("set_toolset", a))


class DriftTest(unittest.TestCase):
    def test_diff_only_reports_changed_fields(self):
        desired = {"description": "r", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:a", "skills": ["x", "y"], "toolsets": [], "mcps": []}
        live = dict(desired, skills=["x"], soul_sha256="sha256:b")
        d = diff_managed(desired, live)
        self.assertEqual({x["field"] for x in d}, {"skills", "soul_sha256"})

    def test_drift_scan_job(self):
        jobs = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), FakeHermes())
        out = jobs.dispatch({"kind": "drift_scan", "params": {"managed": {
            "ok": {"description": "r", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:a", "skills": ["x"], "toolsets": [], "mcps": []},
            "drifted": {"description": "r", "model": {"provider": "local", "name": "llama"}, "soul_sha256": "sha256:zzz", "skills": ["x"], "toolsets": [], "mcps": []},
            "missing": {}}}})
        self.assertTrue(out["ok"])
        self.assertNotIn("ok", out["drift"])
        self.assertEqual(out["drift"]["drifted"][0]["field"], "soul_sha256")
        self.assertEqual(out["drift"]["missing"][0]["field"], "profile")


class ApplyTest(unittest.TestCase):
    def test_apply_stops_at_first_failure_and_keeps_snapshot(self):
        h = FakeHermes()
        jobs = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h)
        out = jobs.dispatch({"kind": "apply", "params": {"changes": [
            {"op": "ensure_profile", "profile": "p", "provider": "local", "model": "llama"},
            {"op": "set_skill", "profile": "p", "skill": "boom"},
            {"op": "write_soul", "profile": "p", "content": "never reached"}]}})
        self.assertFalse(out["ok"])
        self.assertEqual(out["stopped_at"], 1)
        self.assertTrue(out["snapshot"].endswith("snap.tar.gz"))
        self.assertEqual([c[0] for c in h.calls], ["ensure_profile"])

    def test_unknown_job_kind(self):
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), FakeHermes()).dispatch({"kind": "nope"})
        self.assertFalse(out["ok"])


class PolicyPushTest(unittest.TestCase):
    def test_policy_written_where_plugin_reads(self):
        h = FakeHermes()
        jobs = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h)
        out = jobs.dispatch({"kind": "push_policy", "params": {"profiles": {"default": {"deny_tools": ["web.fetch"]}, "screener": {"deny_tools": []}}}})
        self.assertEqual(sorted(out["written"]), ["default", "screener"])
        with open(os.path.join(h.cfg.hermes_home, "fleetcontrol", "policy.json")) as f:
            self.assertEqual(json.load(f)["deny_tools"], ["web.fetch"])
        self.assertTrue(os.path.exists(os.path.join(h.cfg.hermes_home, "profiles", "screener", "fleetcontrol", "policy.json")))


class SocketTest(unittest.TestCase):
    def test_plugin_events_land_in_queue(self):
        path = os.path.join(tempfile.mkdtemp(), "agent.sock")
        q = queue.Queue()
        PluginSocketServer(path, q).start()
        for _ in range(50):
            if os.path.exists(path): break
            time.sleep(0.05)
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(path)
        s.sendall(b'{"kind":"tool.post","tool":"x"}\n{"kind":"session.end"}\n'); s.close()
        got = [q.get(timeout=2), q.get(timeout=2)]
        self.assertEqual([g["kind"] for g in got], ["tool.post", "session.end"])


class RouteTableTest(unittest.TestCase):
    def test_routes_are_well_formed(self):
        for table in (ROUTES, API_ROUTES):
            for k, (m, p) in table.items():
                self.assertIn(m, ("GET", "POST", "PUT", "DELETE"), k)
                self.assertTrue(p.startswith("/"), k)


if __name__ == "__main__":
    unittest.main()
