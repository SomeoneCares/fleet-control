"""Unit tests for the daemon pieces that don't need a Hermes host."""
import hashlib, json, os, socket, tempfile, threading, time, unittest, queue

from fleetctl_agent.hermes_local import (
    API_ROUTES, DASHBOARD_SESSION_HEADER, ROUTES, HermesLocal, HermesLocalConfig, HermesLocalError, diff_managed,
)
from fleetctl_agent.daemon import Jobs, AgentConfig, PluginSocketServer

CAPTURE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "docs", "dashboard-capture-0.21.2-write.json")


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


class CapturedDashboard(HermesLocal):
    """HermesLocal whose HTTP layer replays responses recorded on a real Hermes 0.21.2 host."""

    def __init__(self, overrides=None):
        super().__init__(HermesLocalConfig(dashboard_token="tok", api_key=None, hermes_home=tempfile.mkdtemp()))
        with open(CAPTURE, encoding="utf-8") as f:
            calls = json.load(f)["calls"]
        self.responses = {}
        for c in calls:  # first response wins: the state before the write probe changed anything
            self.responses.setdefault((c["method"], c["path"]), c["response"])
        self.responses.update(overrides or {})
        self.sent = []

    def _request(self, base, method, path, body=None, headers=None):
        self.sent.append({"method": method, "path": path, "body": body, "headers": headers or {}})
        if (method, path) not in self.responses:
            raise HermesLocalError(f"{method} {path} -> 404: not in capture")
        return self.responses[(method, path)]


ONLY_DEFAULT = {("GET", "/api/profiles"): {"profiles": [
    {"name": "default", "model": "upstage/solar-pro4:free", "provider": "nous", "description": ""}]}}


class CapturedDashboardTest(unittest.TestCase):
    def test_session_header_is_the_one_hermes_accepts(self):
        h = CapturedDashboard()
        h.profiles()
        self.assertEqual(DASHBOARD_SESSION_HEADER, "X-Hermes-Session-Token")
        self.assertEqual(h.sent[0]["headers"], {"X-Hermes-Session-Token": "tok"})

    def test_profiles_list_is_unwrapped(self):
        names = [p["name"] for p in CapturedDashboard().profiles()]
        self.assertEqual(names[0], "default")
        self.assertIn("compliance", names)

    def test_live_state_of_default_profile(self):
        h = CapturedDashboard()
        st = h.live_profile_state("default")
        self.assertEqual(set(st), {"description", "model", "soul_sha256", "skills", "toolsets", "mcps"})
        self.assertEqual(st["model"], {"provider": "nous", "name": "upstage/solar-pro4:free"})
        self.assertEqual(st["mcps"], [])  # {"servers": []} must not read as ["servers"]
        self.assertIn("claude-code", st["skills"])
        self.assertEqual(st["skills"], sorted(st["skills"]))
        self.assertIn("web", st["toolsets"])
        soul = h.responses[("GET", "/api/profiles/default/soul")]["content"]
        self.assertEqual(st["soul_sha256"], "sha256:" + hashlib.sha256((soul.rstrip() + "\n").encode()).hexdigest())

    def test_missing_profile_raises(self):
        with self.assertRaises(HermesLocalError):
            CapturedDashboard().live_profile_state("no-such-profile")

    def test_mcp_servers_parsing(self):
        h = CapturedDashboard({("GET", "/api/mcp/servers?profile=default"): {"servers": [
            {"name": "opensanctions", "enabled": True}, {"name": "case-store", "enabled": True},
            {"name": "retired", "enabled": False}]}})
        self.assertEqual(h.mcp_servers("default"), ["case-store", "opensanctions"])

    def test_create_reports_rejected_model(self):
        # Recorded: POST /api/profiles answers 200 with model_set=false when the provider has no credentials.
        h = CapturedDashboard()
        with self.assertRaises(HermesLocalError) as cm:
            h.ensure_profile("fleetcontrol-probe", "Fleet Control probe (safe to delete)", "openai", "gpt-4o-mini")
        self.assertIn("model not set", str(cm.exception))
        post = [s for s in h.sent if s["method"] == "POST"][0]
        self.assertEqual(post["body"], {"name": "fleetcontrol-probe", "description": "Fleet Control probe (safe to delete)",
                                        "provider": "openai", "model": "gpt-4o-mini"})

    def test_import_profiles_job(self):
        jobs = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), CapturedDashboard(ONLY_DEFAULT))
        out = jobs.dispatch({"kind": "import_profiles"})
        self.assertTrue(out["ok"], out)
        self.assertEqual(list(out["profiles"]), ["default"])
        self.assertTrue(out["profiles"]["default"]["soul_text"].startswith("You are Hermes Agent"))


if __name__ == "__main__":
    unittest.main()
