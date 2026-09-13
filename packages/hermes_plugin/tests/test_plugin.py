"""Exercises the plugin with a fake Hermes plugin context and a fake fleetctl-agent socket."""

import json
import os
import socket
import tempfile
import threading
import time
import unittest


class FakeCtx:
    def __init__(self):
        self.hooks = {}

    def register_hook(self, name, cb):
        self.hooks[name] = cb


class PluginTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["HERMES_HOME"] = self.tmp
        os.makedirs(os.path.join(self.tmp, "fleetcontrol"))
        self.sock_path = os.path.join(self.tmp, "fleetcontrol", "agent.sock")
        os.environ["FLEETCONTROL_AGENT_SOCKET"] = self.sock_path
        os.environ["FLEETCONTROL_CAPTURE"] = "sanitized"

        # fake daemon
        self.received = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(self.sock_path)
        self.server.listen(1)
        self.server.settimeout(5)

        def serve():
            conn, _ = self.server.accept()
            buf = b""
            conn.settimeout(3)
            try:
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        self.received.append(json.loads(line))
            except socket.timeout:
                pass

        self.t = threading.Thread(target=serve, daemon=True)
        self.t.start()

        # fresh import each test
        import importlib, sys
        sys.modules.pop("fleetcontrol", None)
        import fleetcontrol  # noqa
        self.plugin = importlib.reload(fleetcontrol)
        self.ctx = FakeCtx()
        self.plugin.register(self.ctx)

    def _wait(self, n, timeout=3.0):
        t0 = time.time()
        while len(self.received) < n and time.time() - t0 < timeout:
            time.sleep(0.05)
        return self.received

    def test_registers_documented_hooks(self):
        for h in ("pre_tool_call", "post_tool_call", "subagent_start", "subagent_stop", "on_session_end"):
            self.assertIn(h, self.ctx.hooks)

    def test_post_tool_call_emits_args_and_result_redacted(self):
        self.ctx.hooks["post_tool_call"](
            tool_name="opensanctions.search",
            args={"query": "Zephyr", "api_key": "sk-live-abcdefghijklmnop"},
            result=json.dumps({"match_count": 1}),
            task_id="sess-1",
            duration_ms=42,
        )
        ev = [e for e in self._wait(2) if e["kind"] == "tool.post"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["tool"], "opensanctions.search")
        self.assertIn("redacted", ev[0]["args"])
        self.assertNotIn("abcdefghijklmnop", ev[0]["args"])
        self.assertEqual(ev[0]["session_id"], "sess-1")
        self.assertFalse(ev[0]["error"])

    def test_policy_blocks_denied_tool(self):
        with open(os.path.join(self.tmp, "fleetcontrol", "policy.json"), "w") as f:
            json.dump({"version": 1, "deny_tools": ["web.fetch"], "approve_tools": ["sar.submit"]}, f)
        out = self.ctx.hooks["pre_tool_call"](tool_name="web.fetch", args={}, task_id="s")
        self.assertEqual(out["action"], "block")
        self.assertIn("message", out)
        self.assertNotIn("rule", out)  # only documented keys go back to Hermes
        out = self.ctx.hooks["pre_tool_call"](tool_name="sar.submit", args={}, task_id="s")
        self.assertEqual(out["action"], "approve")
        self.assertTrue(out["rule_key"].startswith("fleetcontrol:"))
        self.assertIsNone(self.ctx.hooks["pre_tool_call"](tool_name="read_file", args={}, task_id="s"))

    def test_no_policy_file_means_no_decisions(self):
        self.assertIsNone(self.ctx.hooks["pre_tool_call"](tool_name="terminal", args={}, task_id="s"))

    def test_hooks_never_raise(self):
        # bogus types must not propagate into Hermes
        self.ctx.hooks["post_tool_call"](tool_name=None, args=None, result=None, task_id=None, duration_ms=None)
        self.ctx.hooks["subagent_stop"](parent_session_id="p", child_status="completed")

    def tearDown(self):
        self.server.close()


if __name__ == "__main__":
    unittest.main()
