"""Tests for the host-side scripts: capture masking, run_capture's embedded copy, compat checks."""

import json
import os
import tempfile
import unittest

import capture_dashboard_routes as cap
import hermes_compat_check as compat

SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class MaskTest(unittest.TestCase):
    def test_json_fields(self):
        raw = json.dumps({"password_hash": "scrypt$16384$abc", "api_key": "sk-live-0123456789", "secret": "O2cOiVEEKzyO",
                          "redacted_value": "KMXD…[masked]", "max_tokens": 20000, "name": "admin", "session_key": ""})
        out = json.loads(cap.mask(raw))
        for k in ("password_hash", "api_key", "secret"):
            self.assertEqual(out[k], "***masked***", k)
        self.assertEqual(out["session_key"], "")  # empty means unset; nothing to hide
        self.assertEqual(out["redacted_value"], "***")
        self.assertEqual(out["max_tokens"], 20000)
        self.assertEqual(out["name"], "admin")

    def test_yaml_lines_inside_config_raw(self):
        yaml_text = ("dashboard:\n  basic_auth:\n    username: admin\n    password_hash: scrypt$16384$x\n"
                     "    secret: O2cOiVEEK\nagent:\n  max_tokens: 150\nsecurity:\n  redact_secrets: false\n"
                     "API_SERVER_KEY=abcdefghijkl\n")
        text = json.loads(cap.mask(json.dumps({"yaml": yaml_text})))["yaml"]
        self.assertIn("password_hash: ***masked***", text)
        self.assertIn("secret: ***masked***", text)
        self.assertIn("API_SERVER_KEY=***masked***", text)
        self.assertIn("username: admin", text)
        self.assertIn("max_tokens: 150", text)
        self.assertIn("redact_secrets: false", text)
        self.assertNotIn("O2cOiVEEK", text)

    def test_telegram_bot_token_anywhere(self):
        token = "1234567890:" + "A" * 35
        out = cap.mask(json.dumps({"note": f"bot {token} here"}))
        self.assertNotIn(token, out)

    def test_idempotent(self):
        raw = json.dumps({"yaml": "  secret: abcdef\n", "password": "hunter22"})
        self.assertEqual(cap.mask(cap.mask(raw)), cap.mask(raw))


class RunCaptureTest(unittest.TestCase):
    def test_embedded_capture_script_matches(self):
        with open(os.path.join(SCRIPTS, "run_capture.sh"), encoding="utf-8") as f:
            sh = f.read()
        start = sh.index("<<'PYEOF'\n") + len("<<'PYEOF'\n")
        embedded = sh[start:sh.index("\nPYEOF\n", start)]
        with open(os.path.join(SCRIPTS, "capture_dashboard_routes.py"), encoding="utf-8") as f:
            self.assertEqual(embedded, f.read().rstrip("\n"), "run_capture.sh embeds a stale copy of capture_dashboard_routes.py")


class CompatTest(unittest.TestCase):
    def _tree(self, web_server="", web_models=""):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "hermes_cli"))
        for name, text in (("web_server.py", web_server), ("web_models.py", web_models)):
            with open(os.path.join(root, "hermes_cli", name), "w", encoding="utf-8") as f:
                f.write(text)
        return root

    def test_header_matches_daemon(self):
        ok, _ = compat._hdr(self._tree(web_server='_SESSION_HEADER_NAME = "X-Hermes-Session-Token"\n'))
        self.assertTrue(ok)

    def test_shorter_header_is_not_accepted_as_a_prefix(self):
        ok, detail = compat._hdr(self._tree(web_server='_SESSION_HEADER_NAME = "X-Hermes-Session"\n'))
        self.assertFalse(ok)
        self.assertIn("X-Hermes-Session-Token", detail)

    def _skill_utils(self, text):
        root = self._tree()
        os.makedirs(os.path.join(root, "agent"))
        with open(os.path.join(root, "agent", "skill_utils.py"), "w", encoding="utf-8") as f:
            f.write(text)
        return root

    def _mcp(self, summary):
        root = self._tree()
        with open(os.path.join(root, "hermes_cli", "web_server_mcp.py"), "w", encoding="utf-8") as f:
            f.write(f"def _mcp_server_summary(name, cfg):\n    return {{\n{summary}    }}\n\n\ndef _next():\n    pass\n")
        return root

    def test_mcp_summary_fields_copy_mcp_relies_on(self):
        good = ('        "url": cfg.get("url"),\n        "command": cfg.get("command"),\n        "args": list(cfg.get("args") or []),\n'
                '        "env": _redact_mcp_env(cfg.get("env") or {}),\n        "auth": auth,\n')
        self.assertEqual(compat._mcp_summary(self._mcp(good)), (True, "ok"))
        ok, detail = compat._mcp_summary(self._mcp(good.replace("_redact_mcp_env(", "dict(")))
        self.assertFalse(ok)
        self.assertIn("no longer masked", detail)

    def test_essential_skills_match_daemon(self):
        ok, _ = compat._essential(self._skill_utils('ESSENTIAL_SKILLS: frozenset = frozenset({"hermes-agent"})\n'))
        self.assertTrue(ok)

    def test_a_new_essential_skill_fails(self):
        ok, detail = compat._essential(self._skill_utils('ESSENTIAL_SKILLS = frozenset({"hermes-agent", "memory-core"})\n'))
        self.assertFalse(ok)
        self.assertIn("memory-core", detail)
        self.assertFalse(compat._essential(self._tree())[0])  # file gone

    def test_request_bodies(self):
        models = "".join(f"class {c}(BaseModel):\n" + "".join(f"    {f}: str\n" for f in fs) + "\n\n"
                         for c, fs in compat.REQUEST_BODIES.items())
        self.assertTrue(compat._bodies(self._tree(web_models=models))[0])
        ok, detail = compat._bodies(self._tree(web_models=models.replace("    enabled: str\n", "    is_on: str\n")))
        self.assertFalse(ok)
        self.assertIn("SkillToggle.enabled", detail)


if __name__ == "__main__":
    unittest.main()
