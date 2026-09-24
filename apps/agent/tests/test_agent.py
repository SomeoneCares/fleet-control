"""Unit tests for the daemon pieces that don't need a Hermes host."""
import hashlib, json, os, socket, tempfile, threading, time, unittest, queue
from unittest import mock

from fleetctl_agent.hermes_local import (
    API_ROUTES, DASHBOARD_SESSION_HEADER, ROUTES, HermesLocal, HermesLocalConfig, HermesLocalError, diff_managed, dotenv_value,
    tool_calls_from_messages,
)
import urllib.error

from fleetctl_agent.daemon import AgentDaemon, Jobs, AgentConfig, PluginSocketServer, diagnose_endpoint

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

    def test_sync_ops_reach_hermes(self):
        h = FakeHermes()
        h.sync_skills = lambda *a: h.calls.append(("sync_skills", a))
        h.sync_toolsets = lambda *a: h.calls.append(("sync_toolsets", a))
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).dispatch({"kind": "apply", "params": {"changes": [
            {"op": "sync_skills", "profile": "p", "skills": ["a"]},
            {"op": "sync_toolsets", "profile": "p", "toolsets": []}]}})
        self.assertTrue(out["ok"])
        self.assertEqual(h.calls, [("sync_skills", ("p", ["a"])), ("sync_toolsets", ("p", []))])

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
        q = queue.Queue()
        if hasattr(socket, "AF_UNIX"):
            path = os.path.join(tempfile.mkdtemp(), "agent.sock")
            PluginSocketServer(path, q).start()
            for _ in range(50):
                if os.path.exists(path): break
                time.sleep(0.05)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); s.connect(path)
        else:  # Windows: the daemon listens on loopback TCP, as AgentConfig.resolved_socket() does there
            probe = socket.socket(); probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]; probe.close()
            PluginSocketServer(f"tcp://127.0.0.1:{port}", q).start()
            for _ in range(50):
                try:
                    s = socket.create_connection(("127.0.0.1", port), timeout=1); break
                except OSError:
                    time.sleep(0.05)
        s.sendall(b'{"kind":"tool.post","tool":"x"}\n{"kind":"session.end"}\n'); s.close()
        got = [q.get(timeout=2), q.get(timeout=2)]
        self.assertEqual([g["kind"] for g in got], ["tool.post", "session.end"])


class SyncTest(unittest.TestCase):
    class Stub(HermesLocal):
        """Dashboard with a fresh profile's defaults: most things on."""
        def __init__(self):
            super().__init__(HermesLocalConfig(hermes_home=tempfile.mkdtemp()))
            self.toggled = []
        def dashboard(self, route, body=None, **params):
            if route == "toolsets.list":
                return [{"name": "web", "enabled": True}, {"name": "file", "enabled": True}, {"name": "memory", "enabled": False}]
            if route == "skills.list":
                return [{"name": "a", "enabled": True}, {"name": "b", "enabled": False}, {"name": "hermes-agent", "enabled": True}]
            self.toggled.append((route, params.get("toolset") or body.get("name"), body["enabled"]))

    def test_toolsets_end_up_exactly_as_wanted(self):
        h = self.Stub()
        self.assertEqual(h.sync_toolsets("p", ["memory"]), ["file", "memory", "web"])
        self.assertEqual(h.toggled, [("toolsets.toggle", "file", False), ("toolsets.toggle", "memory", True),
                                     ("toolsets.toggle", "web", False)])

    def test_skills_end_up_exactly_as_wanted(self):
        h = self.Stub()
        self.assertEqual(h.sync_skills("p", ["b"]), ["a", "b"])
        self.assertEqual(h.toggled, [("skills.toggle", "a", False), ("skills.toggle", "b", True)])

    def test_essential_skills_are_left_alone(self):
        h = self.Stub()
        self.assertEqual(h.sync_skills("p", ["hermes-agent"]), ["a"])  # listing it is harmless, not an error
        self.assertEqual(h.toggled, [("skills.toggle", "a", False)])
        desired = {"skills": [], "toolsets": []}
        self.assertEqual(diff_managed(desired, {"skills": ["hermes-agent"], "toolsets": []}), [])
        self.assertEqual(diff_managed(desired, {"skills": ["hermes-agent", "x"], "toolsets": []}),
                         [{"field": "skills", "blueprint": [], "live": ["x"]}])

    def test_unknown_name_fails_before_any_toggle(self):
        h = self.Stub()
        with self.assertRaises(HermesLocalError):
            h.sync_toolsets("p", ["web", "no-such-toolset"])
        self.assertEqual(h.toggled, [])


class HermesRunTest(unittest.TestCase):
    class Stub(HermesLocal):
        """API server that answers the create call, then the queued status list, one per poll. The fc-architect
        profile has its own API_SERVER_KEY, as Hermes 0.21.2 requires for /p/<profile>/ requests."""
        def __init__(self, statuses):
            super().__init__(HermesLocalConfig(hermes_home=tempfile.mkdtemp(), api_key="default-key"))
            os.makedirs(os.path.join(self.cfg.hermes_home, "profiles", "fc-architect"))
            with open(os.path.join(self.cfg.hermes_home, "profiles", "fc-architect", ".env"), "w") as f:
                f.write("OPENROUTER_API_KEY=x\nAPI_SERVER_KEY='profile-key'\n")
            self.statuses, self.sent = list(statuses), []
        def _request(self, base, method, path, body=None, headers=None):
            self.sent.append((method, path, body, (headers or {}).get("Authorization")))
            return {"run_id": "run_1", "status": "queued"} if method == "POST" else self.statuses.pop(0)

    def test_named_profiles_use_the_multiplex_prefix_their_own_key_and_are_waited_for(self):
        h = self.Stub([{"status": "running"}, {"status": "completed", "output": "{}", "usage": {"total_tokens": 9}, "session_id": "s1"}])
        out = h.run_agent("fc-architect", "the mission", "the contract", timeout=5, poll_seconds=0)
        self.assertEqual(out, {"run_id": "run_1", "status": "completed", "output": "{}", "error": None, "usage": {"total_tokens": 9},
                               "session_id": "s1"})
        self.assertEqual([s[:2] for s in h.sent], [("POST", "/p/fc-architect/v1/runs"), ("GET", "/p/fc-architect/v1/runs/run_1"),
                                                   ("GET", "/p/fc-architect/v1/runs/run_1")])
        self.assertEqual(h.sent[0][2], {"input": "the mission", "instructions": "the contract"})
        self.assertEqual({s[3] for s in h.sent}, {"Bearer profile-key"})

    def test_a_named_profile_without_its_own_key_is_refused_before_calling_hermes(self):
        h = self.Stub([])
        with self.assertRaises(HermesLocalError) as ctx:
            h.run_agent("bid-orchestrator", "x", timeout=1, poll_seconds=0)
        self.assertIn("API_SERVER_KEY", str(ctx.exception))
        self.assertEqual(h.sent, [])

    def test_the_default_profile_has_no_prefix_and_a_slow_run_times_out(self):
        h = self.Stub([{"status": "running"}] * 500)
        out = h.run_agent("default", "x", timeout=0.05, poll_seconds=0.005)
        self.assertEqual((out["status"], h.sent[0][1], h.sent[0][3]), ("timeout", "/v1/runs", "Bearer default-key"))

    def test_hermes_run_job_reports_a_failed_run(self):
        h = FakeHermes()
        h.profile_api_key = lambda p: "k"
        h.run_agent = lambda *a: {"run_id": "r", "status": "failed", "output": None, "error": "provider down", "usage": None}
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).dispatch({"kind": "hermes_run", "params": {"profile": "p", "input": "i"}})
        self.assertEqual((out["ok"], out["error"]), (False, "provider down"))
        self.assertNotIn("notes", out)

    def test_hermes_run_job_gives_a_profile_without_a_key_its_own(self):
        h = FakeHermes()
        h.profile_api_key = lambda p: None
        h.ensure_profile_api_key = lambda p: h.calls.append(("ensure_key", p)) or ("new", True)
        h.run_agent = lambda *a: {"run_id": "r", "status": "completed", "output": "{}", "error": None, "usage": None}
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).dispatch({"kind": "hermes_run", "params": {"profile": "fc-architect", "input": "i"}})
        self.assertTrue(out["ok"])
        self.assertEqual(h.calls, [("ensure_key", "fc-architect")])
        self.assertIn("API_SERVER_KEY", out["notes"][0])

    def test_hermes_run_job_returns_the_tool_calls_when_asked(self):
        h = FakeHermes()
        h.profile_api_key = lambda p: "k"
        h.run_agent = lambda *a: {"run_id": "r", "status": "completed", "output": "{}", "error": None, "usage": None, "session_id": "s1"}
        h.session_messages = lambda profile, sid: [
            {"role": "assistant", "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "tool_name": "web_search", "content": "…"}]
        jobs = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h)
        out = jobs.dispatch({"kind": "hermes_run", "params": {"profile": "p", "input": "i", "transcript": True}})
        self.assertEqual([c["name"] for c in out["tool_calls"]], ["web_search"])
        self.assertNotIn("tool_calls", jobs.dispatch({"kind": "hermes_run", "params": {"profile": "p", "input": "i"}}))
        h.session_messages = lambda profile, sid: (_ for _ in ()).throw(HermesLocalError("404"))
        out = jobs.dispatch({"kind": "hermes_run", "params": {"profile": "p", "input": "i", "transcript": True}})
        self.assertNotIn("tool_calls", out)  # unknown is not "none"
        self.assertIn("404", out["evidence_error"])


class EvidenceTest(unittest.TestCase):
    TRANSCRIPT = [
        {"role": "user", "content": "Screen Zephyr Maritime Ltd"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "mcp_opensanctions__search", "arguments": "{\"q\": \"Zephyr\"}"}},
            {"id": "c2", "type": "function", "function": {"name": "write_file", "arguments": {"path": "screening-result.json"}}}]},
        {"role": "tool", "tool_call_id": "c1", "tool_name": "mcp_opensanctions__search", "content": "0 matches"},
        {"role": "assistant", "content": "Done.", "tool_calls": json.dumps([{"id": "c3", "function": {"name": "web_search", "arguments": "{}"}}])},
    ]

    def test_tool_calls_come_out_in_order_with_their_results(self):
        calls = tool_calls_from_messages(self.TRANSCRIPT)
        self.assertEqual([c["name"] for c in calls], ["mcp_opensanctions__search", "write_file", "web_search"])
        self.assertEqual((calls[0]["result"], calls[0]["answered"]), ("0 matches", True))
        self.assertEqual((calls[1]["arguments"], calls[1]["answered"]), ('{"path": "screening-result.json"}', False))

    # Captured from a real run on hermesbo-lab-01 (Hermes 0.21.2) against the SAS Viya MCP server:
    # the MCP tool is not on the message, it is inside a dispatcher call named "tool_call".
    DISPATCHED = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "d1", "type": "function", "function": {
                "name": "tool_call",
                "arguments": '{"calls": [{"arguments": {"server_id": "cas-shared-default"}, '
                             '"name": "mcp__sas_viya__list_caslibs"}]}'}}]},
        {"role": "tool", "tool_call_id": "d1", "content": '{"caslibs": ["Public", "Samples"]}'},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "d2", "type": "function", "function": {
                "name": "tool_call",
                "arguments": {"calls": [{"name": "mcp__sas_viya__list_cas_servers", "arguments": {}},
                                        {"name": "web_fetch", "arguments": {"url": "http://example.org"}}]}}}]},
    ]

    def test_a_dispatcher_becomes_the_mcp_tools_it_actually_called(self):
        calls = tool_calls_from_messages(self.DISPATCHED)
        self.assertEqual([c["name"] for c in calls],
                         ["mcp__sas_viya__list_caslibs", "mcp__sas_viya__list_cas_servers", "web_fetch"])
        # the dispatcher's own name never reaches a test: it would make every MCP call look identical
        self.assertNotIn("tool_call", [c["name"] for c in calls])
        self.assertEqual(calls[0]["arguments"], '{"server_id": "cas-shared-default"}')
        self.assertEqual((calls[0]["result"], calls[0]["answered"]), ('{"caslibs": ["Public", "Samples"]}', True))
        # several inner calls share the one result the dispatcher returned, and an unanswered one says so
        self.assertEqual([c["answered"] for c in calls[1:]], [False, False])

    def test_a_call_that_is_not_a_dispatcher_is_left_alone(self):
        calls = tool_calls_from_messages([
            {"role": "assistant", "tool_calls": [
                {"id": "x", "function": {"name": "tool_call", "arguments": "not json"}},
                {"id": "y", "function": {"name": "tool_call", "arguments": '{"no_calls_here": 1}'}}]}])
        self.assertEqual([c["name"] for c in calls], ["tool_call", "tool_call"])

    def test_transcripts_are_read_page_by_page_oldest_first(self):
        h = HermesLocal(HermesLocalConfig(hermes_home=tempfile.mkdtemp(), api_key="k"))
        pages = [{"data": [{"role": "user"}] * 500}, {"data": [{"role": "assistant"}] * 20}]
        sent = []
        h._request = lambda base, method, path, body=None, headers=None: sent.append(path) or pages.pop(0)
        self.assertEqual(len(h.session_messages("default", "s/1")), 520)
        self.assertEqual(sent, ["/api/sessions/s%2F1/messages?order=oldest&limit=500&offset=0",
                                "/api/sessions/s%2F1/messages?order=oldest&limit=500&offset=500"])

    def test_run_test_job_returns_the_evidence(self):
        h = FakeHermes()
        h.profile_api_key = lambda p: "k"
        h.run_agent = lambda *a: {"run_id": "r", "status": "completed", "output": "ok", "error": None, "usage": {"total_tokens": 50}, "session_id": "s1"}
        h.session_messages = lambda profile, sid: self.TRANSCRIPT
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).dispatch({"kind": "run_test", "params": {"profile": "screener", "scenario": "x"}})
        self.assertEqual((out["ok"], out["evidence"], len(out["tool_calls"])), (True, "transcript", 3))
        self.assertGreaterEqual(out["duration_s"], 0)
        def unreadable(profile, sid):
            raise HermesLocalError("GET … -> 404")
        h.session_messages = unreadable
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).dispatch({"kind": "run_test", "params": {"profile": "screener", "scenario": "x"}})
        self.assertEqual((out["evidence"], out["tool_calls"]), ("none", None))
        self.assertIn("404", out["evidence_error"])


class ProfileKeyTest(unittest.TestCase):
    def setUp(self):
        self.h = HermesLocal(HermesLocalConfig(hermes_home=tempfile.mkdtemp(), api_key="default-key"))
        self.dir = os.path.join(self.h.cfg.hermes_home, "profiles", "fc-architect")
        os.makedirs(self.dir)
        self.env = os.path.join(self.dir, ".env")

    def test_dotenv_values(self):
        with open(self.env, "w") as f:
            f.write("# API_SERVER_KEY=commented\nexport API_SERVER_KEY=\"first\"\nOTHER=1\nAPI_SERVER_KEY='second'\n")
        self.assertEqual(dotenv_value(self.env, "API_SERVER_KEY"), "second")
        self.assertIsNone(dotenv_value(self.env, "MISSING"))
        self.assertIsNone(dotenv_value(os.path.join(self.dir, "nope.env"), "API_SERVER_KEY"))
        self.assertEqual(self.h.profile_api_key("default"), "default-key")

    def test_a_key_is_created_once_and_kept_private(self):
        with open(self.env, "w") as f:
            f.write("OPENROUTER_API_KEY=x")  # no trailing newline
        key, created = self.h.ensure_profile_api_key("fc-architect")
        self.assertTrue(created and len(key) >= 32)
        self.assertEqual(self.h.ensure_profile_api_key("fc-architect"), (key, False))
        with open(self.env) as f:
            self.assertEqual(f.read(), f"OPENROUTER_API_KEY=x\nAPI_SERVER_KEY={key}\n")
        if os.name != "nt":
            self.assertEqual(os.stat(self.env).st_mode & 0o777, 0o600)
        with self.assertRaises(HermesLocalError):
            self.h.ensure_profile_api_key("no-such-profile")



class EndpointDiagnosisTest(unittest.TestCase):
    """Hermes reports a dead upstream with an empty error; the agent says which layer failed instead."""

    def _serve(self, status):
        import http.server
        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(status); self.send_header("Content-Length", "0"); self.end_headers()
            def log_message(self, *a): pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_address[1]}/mcp"

    def test_a_closed_port_is_the_server_being_down(self):
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        msg = diagnose_endpoint(f"http://127.0.0.1:{port}/mcp", timeout=2)
        self.assertIn(f"cannot connect to 127.0.0.1:{port}", msg)
        self.assertIn("not the credentials", msg)

    def test_a_502_is_the_server_behind_the_ingress(self):
        self.assertIn("HTTP 502", diagnose_endpoint(self._serve(502)))
        self.assertIn("not the credentials", diagnose_endpoint(self._serve(502)))

    def test_a_401_to_a_credential_less_check_is_not_an_outage(self):
        msg = diagnose_endpoint(self._serve(401))
        self.assertIn("HTTP 401", msg)
        self.assertIn("login", msg)

    def test_discovery_fills_a_blank_error(self):
        h = FakeHermes()
        h.mcp_list = lambda profile: [{"name": "sas-viya", "enabled": True, "url": "http://viya.example/mcp"},
                                      {"name": "local", "enabled": True, "command": "npx"}]
        h.mcp_probe = lambda profile, server: {"ok": False, "error": ""}
        with mock.patch("fleetctl_agent.daemon.diagnose_endpoint", return_value="cannot connect") as diag:
            out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).mcp_discover({"profiles": ["analyst"]})
        diag.assert_called_once_with("http://viya.example/mcp")
        rows = {r["name"]: r for r in out["servers"]["analyst"]}
        self.assertEqual(rows["sas-viya"]["error"], "cannot connect")
        self.assertIn("no answer", rows["local"]["error"])


class McpCopyTest(unittest.TestCase):
    """copy_mcp registers a server with another profile's configuration, and never copies a secret."""

    def _hermes(self, servers):
        h = HermesLocal(HermesLocalConfig())
        added = []
        h.profiles = lambda: [{"name": p} for p in ("default", "analyst", "writer")]
        h.mcp_list = lambda profile: servers.get(profile, [])
        h.mcp_add = lambda profile, config: added.append((profile, config)) or {}
        return h, added

    def test_a_url_server_is_copied_with_its_auth_and_the_login_is_left_to_the_profile(self):
        h, added = self._hermes({"analyst": [{"name": "sas-viya", "url": "https://viya/mcp", "auth": "oauth", "env": {}}]})
        self.assertEqual(h.mcp_copy("writer", "sas-viya", None), {"from": "analyst", "login_needed": True})
        self.assertEqual(added, [("writer", {"name": "sas-viya", "url": "https://viya/mcp", "auth": "oauth"})])

    def test_already_registered_is_a_no_op(self):
        h, added = self._hermes({"writer": [{"name": "sas-viya", "url": "u"}]})
        self.assertEqual(h.mcp_copy("writer", "sas-viya", "analyst"), {"already": True})
        self.assertEqual(added, [])

    def test_secrets_are_never_copied(self):
        h, added = self._hermes({"analyst": [{"name": "crm", "url": "https://crm/mcp", "auth": "header"}],
                                 "default": [{"name": "fs", "command": "npx", "args": ["fs"], "env": {"API_KEY": "***"}},
                                             {"name": "calc", "command": "calc-mcp", "args": ["--safe"], "env": {}}]})
        with self.assertRaisesRegex(HermesLocalError, "header token"):
            h.mcp_copy("writer", "crm", None)
        with self.assertRaisesRegex(HermesLocalError, "API_KEY"):
            h.mcp_copy("writer", "fs", None)
        with self.assertRaisesRegex(HermesLocalError, "not configured on any profile"):
            h.mcp_copy("writer", "nowhere", None)
        self.assertEqual(h.mcp_copy("writer", "calc", None), {"from": "default", "login_needed": False})
        self.assertEqual(added, [("writer", {"name": "calc", "command": "calc-mcp", "args": ["--safe"]})])

    def test_apply_runs_the_mcp_ops(self):
        h = FakeHermes()
        h.mcp_copy = lambda profile, server, src: {"from": src, "login_needed": False}
        h.mcp_list = lambda profile: [{"name": "old"}]
        removed = []
        h.mcp_remove = lambda profile, server: removed.append((profile, server))
        out = Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h).apply({"snapshot": False, "changes": [
            {"op": "copy_mcp", "profile": "writer", "server": "sas-viya", "from_profile": "analyst"},
            {"op": "remove_mcp", "profile": "writer", "server": "old"},
            {"op": "remove_mcp", "profile": "writer", "server": "never-there"}]})
        self.assertTrue(all(r["ok"] for r in out["results"]), out)
        self.assertEqual(out["results"][0]["from"], "analyst")
        self.assertEqual(removed, [("writer", "old")])


class MessagingTest(unittest.TestCase):
    """Fleet Control's channels are deliver_only webhook routes; their secrets stay in the agent's state dir."""

    def _jobs(self, routes=None, enabled=True):
        h = FakeHermes()
        state = {"routes": list(routes or [])}
        h.created, h.deleted, h.posted = [], [], []
        h.gateway_runtime = lambda: None
        h.messaging_state = lambda: {"platforms": [{"id": "telegram", "state": "connected"}],
                                     "webhooks": {"enabled": enabled, "base_url": "http://localhost:8644", "routes": list(state["routes"])}}

        def create(route, platform, chat_id, secret, description):
            h.created.append((route, platform, chat_id, secret))
            state["routes"].append({"name": route, "url": f"http://localhost:8644/webhooks/{route}"})
            return {"url": f"http://localhost:8644/webhooks/{route}"}

        h.webhook_create = create
        h.webhook_delete = lambda route: (h.deleted.append(route), state["routes"].__setitem__(
            slice(None), [r for r in state["routes"] if r["name"] != route]))
        h.webhook_post = lambda url, payload, secret, rid: (h.posted.append((url, payload, secret, rid)) or (200, {"status": "delivered"}))
        return Jobs(AgentConfig(state_dir=tempfile.mkdtemp()), h), h

    def test_a_route_is_created_with_a_secret_only_this_host_keeps(self):
        jobs, h = self._jobs()
        out = jobs.dispatch({"kind": "channel_route", "params": {"action": "create", "route": "fc-ops", "platform": "telegram", "chat_id": "-100"}})
        self.assertTrue(out["ok"], out)
        self.assertNotIn("secret", json.dumps(out))  # the secret never goes back to Fleet Control
        path = os.path.join(jobs.cfg.state_dir, "route-secrets.json")
        self.assertEqual(json.load(open(path))["fc-ops"], h.created[0][3])
        if os.name != "nt":
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        found = jobs.dispatch({"kind": "messaging_discover", "params": {}})
        self.assertTrue(next(r for r in found["webhooks"]["routes"] if r["name"] == "fc-ops")["signable"])
        # creating again replaces the route and its secret; removing forgets both
        jobs.dispatch({"kind": "channel_route", "params": {"action": "create", "route": "fc-ops", "platform": "telegram"}})
        self.assertEqual(h.deleted, ["fc-ops"])
        jobs.dispatch({"kind": "channel_route", "params": {"action": "remove", "route": "fc-ops"}})
        self.assertNotIn("fc-ops", json.load(open(path)))
        self.assertFalse(jobs.dispatch({"kind": "channel_route", "params": {"action": "remove", "route": "someone-elses"}})["ok"])

    def test_a_message_is_posted_to_the_route_and_its_outcome_reported(self):
        jobs, h = self._jobs()
        jobs.dispatch({"kind": "channel_route", "params": {"action": "create", "route": "fc-ops", "platform": "telegram"}})
        out = jobs.dispatch({"kind": "deliver_message", "params": {"route": "fc-ops", "text": "Decision needed", "delivery_id": "dlv_1"}})
        self.assertEqual((out["ok"], out["status"]), (True, "delivered"))
        url, payload, secret, rid = h.posted[0]
        self.assertEqual((url, payload["text"], rid), ("http://localhost:8644/webhooks/fc-ops", "Decision needed", "dlv_1"))
        h.webhook_post = lambda *a: (502, "Delivery failed")
        out = jobs.dispatch({"kind": "deliver_message", "params": {"route": "fc-ops", "text": "x", "delivery_id": "dlv_2"}})
        self.assertEqual((out["ok"], out["http_status"]), (False, 502))
        self.assertFalse(jobs.dispatch({"kind": "deliver_message", "params": {"route": "fc-none", "text": "x", "delivery_id": "d"}})["ok"])

    def test_nothing_is_posted_while_the_webhook_platform_is_off(self):
        jobs, h = self._jobs(enabled=False)
        jobs._save_route_secrets({"fc-ops": "s"})
        out = jobs.dispatch({"kind": "deliver_message", "params": {"route": "fc-ops", "text": "x", "delivery_id": "d"}})
        self.assertFalse(out["ok"])
        self.assertIn("webhook platform is off", out["error"])
        self.assertEqual(h.posted, [])

    def test_the_signature_is_the_one_hermes_checks(self):
        import hashlib, hmac, http.server
        seen = {}

        class Hermes(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # gateway/platforms/webhook.py generic V2, reimplemented as the test's oracle
                body = self.rfile.read(int(self.headers["Content-Length"]))
                ts, sig = self.headers["X-Webhook-Timestamp"], self.headers["X-Webhook-Signature-V2"]
                want = hmac.new(b"route-secret", ts.encode() + b"." + body, hashlib.sha256).hexdigest()
                seen.update(ok=hmac.compare_digest(sig, want), fresh=abs(time.time() - int(ts)) <= 300,
                            rid=self.headers["X-Request-ID"], body=json.loads(body))
                out = json.dumps({"status": "delivered"}).encode()
                self.send_response(200 if seen["ok"] else 401); self.send_header("Content-Length", str(len(out))); self.end_headers()
                self.wfile.write(out)

            def log_message(self, *a):
                pass

        srv = http.server.HTTPServer(("127.0.0.1", 0), Hermes)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        status, body = HermesLocal(HermesLocalConfig()).webhook_post(
            f"http://127.0.0.1:{srv.server_address[1]}/webhooks/fc-ops", {"text": "hi"}, "route-secret", "dlv_9")
        self.assertEqual((status, body, seen["ok"], seen["fresh"], seen["rid"], seen["body"]["text"]),
                         (200, {"status": "delivered"}, True, True, "dlv_9", "hi"))


class GatewayRecordTest(unittest.TestCase):
    """The gateway's own gateway_state.json, and a dashboard left behind by a Hermes update."""

    def _home(self, record=None, version=None):
        home = tempfile.mkdtemp()
        if record is not None:
            with open(os.path.join(home, "gateway_state.json"), "w", encoding="utf-8") as f:
                json.dump(record, f)
        if version:
            os.makedirs(os.path.join(home, "hermes-agent", "hermes_cli"))
            with open(os.path.join(home, "hermes-agent", "hermes_cli", "__init__.py"), "w", encoding="utf-8") as f:
                f.write(f'__version__ = "{version}"\n')
        return HermesLocal(HermesLocalConfig(hermes_home=home))

    def test_a_live_fresh_record_is_believed_and_a_stale_one_is_not(self):
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        rec = {"pid": os.getpid(), "gateway_state": "running", "code_version": "0.21.4", "updated_at": now.isoformat(),
               "platforms": {"telegram": {"state": "connected"}, "webhook": {"state": "fatal", "error_message": "port busy"}}}
        g = self._home(rec).gateway_runtime()
        self.assertTrue(g["alive"])
        self.assertEqual((g["platforms"]["telegram"]["state"], g["platforms"]["webhook"]["error_message"]), ("connected", "port busy"))
        old = self._home({**rec, "updated_at": (now - timedelta(minutes=10)).isoformat()}).gateway_runtime()
        self.assertEqual((old["alive"], old["pid_alive"]), (False, True))  # the process lives, its record is stale
        self.assertIsNone(self._home().gateway_runtime())

    def test_a_dashboard_older_than_the_installed_hermes_is_reported(self):
        h = self._home(version="0.21.4")
        h.dashboard = lambda route, *a, **k: {"version": "0.21.2", "config_version": 7, "auth_required": False}
        h.api = lambda route, *a, **k: {"version": "0.21.4"} if route == "health" else {"features": {}}
        report = h.capability_report()
        self.assertTrue(report["dashboard_stale"])
        self.assertEqual(report["versions"], {"dashboard": "0.21.2", "installed": "0.21.4"})
        self.assertTrue(any("restart" in n and "fleetctl-dashboard" in n for n in report["notes"]))
        h.dashboard = lambda route, *a, **k: {"version": "0.21.4"}
        self.assertNotIn("dashboard_stale", h.capability_report())

class ConfigTest(unittest.TestCase):
    def test_environment_overrides(self):
        env = {"HERMES_BIN": "/opt/hermes/bin/hermes", "HERMES_DASHBOARD_URL": "http://127.0.0.1:9129",
               "HERMES_API_URL": "http://127.0.0.1:8650"}
        with mock.patch.dict(os.environ, env):
            cfg = HermesLocalConfig()
        self.assertEqual((cfg.hermes_bin, cfg.dashboard_url, cfg.api_url),
                         ("/opt/hermes/bin/hermes", "http://127.0.0.1:9129", "http://127.0.0.1:8650"))

    def test_defaults_are_loopback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            cfg = HermesLocalConfig()
        self.assertEqual((cfg.dashboard_url, cfg.api_url), ("http://127.0.0.1:9119", "http://127.0.0.1:8642"))


class PairingTest(unittest.TestCase):
    class FlakyControlPlane:
        def __init__(self, failures):
            self.failures, self.calls = list(failures), 0
        def pair(self, report):
            self.calls += 1
            if self.failures:
                raise self.failures.pop(0)
            return "agent-token"

    def daemon(self, failures):
        d = AgentDaemon(AgentConfig(pairing_token="pair_x", agent_token=None, state_dir=tempfile.mkdtemp()))
        d.hermes = type("H", (), {"capability_report": lambda self: {}})()
        d.cp = self.FlakyControlPlane(failures)
        return d

    def test_waits_for_an_unreachable_control_plane(self):
        refused = urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        d = self.daemon([refused, refused])
        with mock.patch("fleetctl_agent.daemon.time.sleep") as sleep:
            d._pair_if_needed()
        self.assertEqual(d.cfg.agent_token, "agent-token")
        self.assertEqual(d.cp.calls, 3)
        self.assertEqual([c.args[0] for c in sleep.call_args_list], [5, 10])
        with open(os.path.join(d.cfg.state_dir, "agent.token")) as f:
            self.assertEqual(f.read(), "agent-token")

    def test_a_refused_token_fails_at_once(self):
        d = self.daemon([urllib.error.HTTPError("u", 401, "invalid pairing token", {}, None)])
        with mock.patch("fleetctl_agent.daemon.time.sleep") as sleep, self.assertRaises(urllib.error.HTTPError):
            d._pair_if_needed()
        sleep.assert_not_called()


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
    def test_copied_plugin_claims_no_hooks_until_enabled(self):
        h = CapturedDashboard()
        os.makedirs(os.path.join(h.cfg.hermes_home, "plugins", "fleetcontrol"))
        r = h.capability_report()
        self.assertEqual(r["plugins"]["fleetcontrol"], "installed, not enabled")
        self.assertNotIn("hooks", r["capabilities"])
        self.assertNotIn("policy.enforce", r["capabilities"])
        with open(os.path.join(h.cfg.hermes_home, "config.yaml"), "w", encoding="utf-8") as f:
            f.write("plugins:\n  enabled:\n    - telegram_topic_profiles\n    - fleetcontrol\n")
        r = h.capability_report()
        self.assertEqual(r["plugins"]["fleetcontrol"], "enabled")
        self.assertIn("hooks", r["capabilities"])
        self.assertIn("policy.enforce", r["capabilities"])

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
        self.assertNotIn("hermes-agent", st["skills"])  # essential: always on, never managed
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
