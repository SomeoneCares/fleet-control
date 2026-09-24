"""Integrations through FastAPI: discovery, adding and toggling MCP servers, with the agent played by the test."""

import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")
LIVE = {"screener": {"description": "", "model": {"provider": "local", "name": "llama-4"}, "soul_sha256": "sha256:0",
                     "soul_text": "", "skills": [], "toolsets": [], "mcps": ["opensanctions"]}}
FOUND = {"screener": [{"name": "opensanctions", "enabled": True, "transport": "http", "url": "https://os.example/mcp", "ok": True,
                       "tools": [{"name": "search", "description": "Search"}]}]}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class IntegrationsApiTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
        self.inst = "int-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.c.post(f"/api/v1/instances/{self.inst}/import")
        self._agent_answers("import_profiles", {"ok": True, "profiles": LIVE})

    def _agent_answers(self, kind, result):
        job = self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()
        self.assertEqual(job["kind"], kind, job)
        self.assertEqual(self.c.post(f"/agent/v1/jobs/{job['id']}/result", json=result, headers=self.agent).status_code, 200)
        return job

    def _mine(self):
        rows = self.c.get("/api/v1/integrations").json()
        return {(r["kind"], r["name"]): r for r in rows["integrations"]}, rows["discovery"]

    def test_discovery_fills_in_health_and_tools(self):
        rows, discovery = self._mine()
        self.assertEqual(rows[("mcp", "opensanctions")]["health"], "unknown")  # known from the import, not probed yet
        self.assertTrue(next(d for d in discovery if d["instance_id"] == self.inst)["can_discover"])

        r = self.c.post("/api/v1/integrations/discover", json={"instance_id": self.inst})
        self.assertEqual(r.status_code, 200, r.text)
        job = self._agent_answers("mcp_discover", {"ok": True, "servers": FOUND, "at": 1_000_000.0})
        self.assertEqual(job["params"]["profiles"], ["screener"])
        rows, discovery = self._mine()
        row = rows[("mcp", "opensanctions")]
        self.assertEqual((row["health"], [t["name"] for t in row["tools"]], row["endpoint"]), ("healthy", ["search"], "https://os.example/mcp"))
        self.assertEqual(next(d for d in discovery if d["instance_id"] == self.inst)["at"], 1_000_000.0)

    def test_adding_a_server_sends_no_secrets_and_rediscovers(self):
        r = self.c.post("/api/v1/integrations/mcp", json={"instance_id": self.inst, "profile": "screener", "name": "corp-registry",
                                                          "url": "https://registry.example/mcp", "auth": "oauth"})
        self.assertEqual(r.status_code, 201, r.text)
        job = self._agent_answers("mcp_write", {"ok": True, "action": "add", "server": "corp-registry", "result": {}})
        self.assertEqual(job["params"]["config"], {"name": "corp-registry", "url": "https://registry.example/mcp", "auth": "oauth"})
        self.assertNotIn("env", job["params"]["config"])
        self._agent_answers("mcp_discover", {"ok": True, "servers": {"screener": [{"name": "corp-registry", "enabled": True, "ok": True, "tools": []}]},
                                             "at": 1_000_100.0})
        self.assertIn(("mcp", "corp-registry"), self._mine()[0])

        bad = self.c.post("/api/v1/integrations/mcp", json={"instance_id": self.inst, "profile": "screener", "name": "x",
                                                            "url": "u", "command": "c"})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(self.c.post("/api/v1/integrations/mcp", json={"instance_id": self.inst, "profile": "nope", "name": "x",
                                                                       "url": "u"}).status_code, 422)

    def test_enabling_disabling_and_removing(self):
        r = self.c.patch("/api/v1/integrations/mcp/opensanctions", json={"instance_id": self.inst, "profile": "screener", "enabled": False})
        self.assertEqual(r.status_code, 200, r.text)
        job = self._agent_answers("mcp_write", {"ok": True, "action": "disable", "server": "opensanctions", "result": {}})
        self.assertEqual((job["params"]["action"], job["params"]["server"]), ("disable", "opensanctions"))
        self._agent_answers("mcp_discover", {"ok": True, "servers": {"screener": [{"name": "opensanctions", "enabled": False}]}, "at": 2.0})
        self.assertEqual(self._mine()[0][("mcp", "opensanctions")]["enabled_everywhere"], False)

        self.assertEqual(self.c.patch("/api/v1/integrations/mcp/opensanctions",
                                      json={"instance_id": self.inst, "profile": "screener"}).status_code, 422)
        self.assertEqual(self.c.patch("/api/v1/integrations/mcp/opensanctions",
                                      json={"instance_id": self.inst, "profile": "screener", "remove": True}).status_code, 200)
        self._agent_answers("mcp_write", {"ok": True, "action": "remove", "server": "opensanctions", "result": {"ok": True}})
        audit = self.c.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "integrations.mcp_removed" for e in audit))

    def test_rediscovering_one_profile_keeps_the_others_and_a_removal_prunes(self):
        both = {"screener": LIVE["screener"], "writer": LIVE["screener"]}
        self.c.post(f"/api/v1/instances/{self.inst}/import")
        self._agent_answers("import_profiles", {"ok": True, "profiles": both})
        self.c.post("/api/v1/integrations/discover", json={"instance_id": self.inst})
        self._agent_answers("mcp_discover", {"ok": True, "servers": {"screener": FOUND["screener"], "writer": FOUND["screener"]}, "at": 5.0})
        mine = lambda r: [p for p in r["profiles"] if p.startswith(f"{self.inst}/")]
        self.assertEqual(mine(self._mine()[0][("mcp", "opensanctions")]), [f"{self.inst}/screener", f"{self.inst}/writer"])

        self.c.patch("/api/v1/integrations/mcp/opensanctions", json={"instance_id": self.inst, "profile": "writer", "remove": True})
        self._agent_answers("mcp_write", {"ok": True, "action": "remove", "server": "opensanctions", "result": {"ok": True}})
        job = self._agent_answers("mcp_discover", {"ok": True, "servers": {"writer": []}, "at": 6.0})
        self.assertEqual(job["params"]["profiles"], ["writer"])
        row = self._mine()[0][("mcp", "opensanctions")]
        self.assertEqual(mine(row), [f"{self.inst}/screener"])  # writer pruned, screener still there
        health = {e["profile"]: e["health"] for e in row["profile_health"] if e["instance"] == self.inst}
        self.assertEqual(health, {"screener": "healthy"})

    def test_used_by_is_the_applied_version_and_a_newer_draft_is_only_planned(self):
        name = "int-bp-" + os.urandom(3).hex()
        with open(EXAMPLE, encoding="utf-8") as f:
            text = f.read().replace("name: aml-investigation", f"name: {name}")
        self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": text}).status_code, 201)
        store.set_blueprint_status(name, 3, "applied")
        draft = text.replace("version: 3", "version: 4", 1).replace("mcps: [corporate-registry]", "mcps: [corporate-registry, opensanctions]")
        self.assertEqual(self.c.post("/api/v1/blueprints", json={"yaml": draft}).status_code, 201)
        row = self._mine()[0][("mcp", "opensanctions")]
        self.assertEqual([u for u in row["used_by"] if u["blueprint"] == name], [{"agent": "sanctions-screener", "blueprint": name, "version": 3}])
        self.assertEqual([u for u in row["planned_by"] if u["blueprint"] == name], [{"agent": "ownership-tracer", "blueprint": name, "version": 4}])

    def test_who_may_look_and_change(self):
        self.assertEqual(signed_in("viewer").get("/api/v1/integrations").status_code, 403)
        self.assertEqual(signed_in("operator").get("/api/v1/integrations").status_code, 200)
        self.assertEqual(signed_in("approver").post("/api/v1/integrations/discover", json={"instance_id": self.inst}).status_code, 403)
        api_only = "int-api-" + os.urandom(3).hex()
        self.c.post("/api/v1/instances", json={"id": api_only, "environment": "lab", "mode": "api-only"})
        self.assertEqual(self.c.post("/api/v1/integrations/discover", json={"instance_id": api_only}).status_code, 409)


if __name__ == "__main__":
    unittest.main()
