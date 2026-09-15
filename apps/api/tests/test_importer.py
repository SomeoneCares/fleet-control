"""Blueprint from imported live profiles: exact by construction, honest about what it leaves out."""

import hashlib
import os
import unittest

from fleetcontrol_api.importer import LiveImportError, agent_id_for, blueprint_from_live
from fleetcontrol_api.planner import compute_plan

try:
    from fastapi.testclient import TestClient

    from tests.helpers import signed_in
except ImportError:  # pragma: no cover
    TestClient = None


def state(soul, **kw):
    """A profile as the agent's import job reports it."""
    s = {"description": "Routes bids to specialists", "model": {"provider": "nous", "name": "upstage/solar-pro4:free"},
         "soul_sha256": "sha256:" + hashlib.sha256((soul.rstrip() + "\n").encode("utf-8")).hexdigest(), "soul_text": soul,
         "skills": ["pdf", "docx"], "toolsets": ["web", "file"], "mcps": []}
    s.update(kw)
    return s


LIVE = {
    "bid-orchestrator": state("# Bid orchestrator\n\nRoute every bid to the right specialist.\n"),
    "default": state("\n  An indented first line keeps its spaces\n", description="", skills=[], toolsets=[]),
    "Sales_Team": state("Sales.\n"),
}


def build(live=LIVE, **kw):
    return blueprint_from_live(live, name="lab-fleet", owner="dana@example.org", instance_id="lab-01", environment="lab", **kw)


class ImporterTest(unittest.TestCase):
    def test_plans_to_zero_changes_against_the_same_instance(self):
        bp, skipped = build()
        self.assertEqual(skipped, [])
        self.assertEqual(sorted(bp.managed_fields()), ["Sales_Team", "bid-orchestrator", "default"])
        plan = compute_plan(bp, LIVE, target_instance="lab-01", agent_installed=True, environment="lab")
        self.assertEqual([r for r in plan["changes"] if r["kind"] in ("create", "update")], [])
        self.assertEqual(plan["unmanaged_profiles"], [])
        self.assertEqual([(t.instance, t.environment.value) for t in bp.targets], [("lab-01", "lab")])

    def test_soul_is_kept_verbatim_including_leading_whitespace(self):
        bp, _ = build(profiles=["default"])
        self.assertEqual(bp.agent("default").soul.render(), "\n  An indented first line keeps its spaces\n")
        self.assertEqual(bp.agent("default").soul.objective, "An indented first line keeps its spaces")

    def test_profile_names_outside_the_id_rules_keep_their_hermes_name(self):
        bp, _ = build(profiles=["Sales_Team"])
        self.assertEqual((bp.agents[0].id, bp.agents[0].profile_name), ("sales-team", "Sales_Team"))
        self.assertEqual([agent_id_for(p) for p in ("9lives", "x", "ok-name")], ["p-9lives", "x-profile", "ok-name"])

    def test_ids_stay_unique(self):
        bp, _ = build({"sales-team": state("a"), "Sales_Team": state("b")})
        self.assertEqual(sorted(a.id for a in bp.agents), ["sales-team", "sales-team-2"])

    def test_profiles_that_cannot_be_reproduced_are_skipped_with_a_reason(self):
        old = state("y")
        del old["soul_text"]
        live = dict(LIVE, broken=state("x", model={"provider": None, "name": None}), old=old,
                    padded=state("z", description=" trailing space "))
        bp, skipped = build(live, profiles=["bid-orchestrator", "broken", "old", "padded"])
        self.assertEqual([a.id for a in bp.agents], ["bid-orchestrator"])
        reasons = {s["profile"]: s["reason"] for s in skipped}
        self.assertEqual(set(reasons), {"broken", "old", "padded"})
        self.assertIn("model", reasons["broken"])
        self.assertIn("import again", reasons["old"])
        self.assertIn("exactly", reasons["padded"])

    def test_nothing_importable_or_an_unknown_profile_is_an_error(self):
        with self.assertRaises(LiveImportError):
            build(profiles=["nope"])
        with self.assertRaises(LiveImportError):
            build({"b": state("x", model={})})


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class ImportApiTest(unittest.TestCase):
    def setUp(self):
        self.c = signed_in("admin")
        self.inst = "imp-" + os.urandom(3).hex()
        pair = self.c.post("/api/v1/instances", json={"id": self.inst, "environment": "lab", "mode": "agent"}).json()["pairing_token"]
        tok = self.c.post("/agent/v1/pair", json={"instance_id": self.inst, "agent_version": "0.1.0", "report": {}},
                          headers={"Authorization": f"Bearer {pair}"}).json()["agent_token"]
        self.agent = {"Authorization": f"Bearer {tok}"}
        self.name = "imported-" + os.urandom(3).hex()

    def _import(self):
        job = self.c.post(f"/api/v1/instances/{self.inst}/import").json()["job_id"]
        self.assertEqual(self.c.get(f"/agent/v1/instances/{self.inst}/jobs/next", headers=self.agent).json()["id"], job)
        r = self.c.post(f"/agent/v1/jobs/{job}/result", json={"ok": True, "profiles": LIVE}, headers=self.agent)
        self.assertEqual(r.status_code, 200, r.text)

    def _create(self, client=None, **body):
        return (client or self.c).post(f"/api/v1/instances/{self.inst}/blueprint-from-live", json={"name": self.name, **body})

    def test_needs_an_import_first(self):
        self.assertEqual(self._create().status_code, 409)

    def test_creates_a_draft_that_plans_to_no_changes(self):
        self._import()
        r = self._create()
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["version"], r.json()["status"], r.json()["skipped"]), (1, "draft", []))
        self.assertEqual(sorted(r.json()["managed_profiles"]), ["Sales_Team", "bid-orchestrator", "default"])
        plan = self.c.post("/api/v1/plans", json={"blueprint": self.name, "version": 1, "instance_id": self.inst}).json()
        self.assertEqual([x for x in plan["changes"] if x["kind"] in ("create", "update")], [])
        self.assertEqual(self._create().status_code, 409)  # the name is taken
        audit = self.c.get("/api/v1/audit").json()
        self.assertTrue(any(e["action"] == "blueprint.imported" and self.name in e["target"] for e in audit))

    def test_a_chosen_subset_and_a_bad_name(self):
        self._import()
        r = self._create(profiles=["default"])
        self.assertEqual(r.json()["managed_profiles"], ["default"])
        self.name = "Not A Valid Name"
        self.assertEqual(self._create().status_code, 422)

    def test_only_roles_that_write_blueprints(self):
        self._import()
        self.assertEqual(self._create(client=signed_in("operator")).status_code, 403)
        self.assertEqual(self._create(client=signed_in("fleet_architect")).status_code, 201)


if __name__ == "__main__":
    unittest.main()
