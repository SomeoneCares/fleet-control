import os
import time
import unittest

from fleetcontrol_blueprint import dump_blueprint, load_blueprint
from fleetcontrol_api.drift import DriftResolutionError, accept_into_blueprint, live_state_from_drift, select_drift
from fleetcontrol_api.planner import compute_plan
from fleetcontrol_api.store import Store

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")

SKILLS = {"field": "skills", "blueprint": ["list-versioning", "name-matching"], "live": ["list-versioning", "name-matching", "quick-lookup"]}
MODEL = {"field": "model", "blueprint": {"provider": "local", "name": "llama-4-70b-q4"}, "live": {"provider": "local", "name": "llama-4-8b"}}
DRIFT = {"sanctions-screener": [SKILLS], "ownership-tracer": [MODEL]}


class DriftFunctionsTest(unittest.TestCase):
    def setUp(self):
        self.bp = load_blueprint(EXAMPLE)

    def test_select_subset_and_unknown(self):
        self.assertEqual(select_drift(DRIFT, [{"profile": "ownership-tracer", "field": "model"}]), {"ownership-tracer": [MODEL]})
        self.assertEqual(select_drift(DRIFT, []), DRIFT)
        with self.assertRaises(DriftResolutionError):
            select_drift(DRIFT, [{"profile": "challenger", "field": "skills"}])

    def test_revert_plan_restores_only_drifted_fields(self):
        plan = compute_plan(self.bp, live_state_from_drift(self.bp, DRIFT), target_instance="hermes-staging-eu-01",
                            agent_installed=True, environment="staging")
        updates = {r["object"]: r for r in plan["changes"] if r["kind"] == "update"}
        self.assertEqual(set(updates), {"profile sanctions-screener", "profile ownership-tracer"})
        self.assertIn({"op": "set_skill", "profile": "sanctions-screener", "skill": "quick-lookup", "enabled": False},
                      updates["profile sanctions-screener"]["ops"])
        self.assertEqual(updates["profile ownership-tracer"]["ops"][0]["model"], "llama-4-70b-q4")
        self.assertFalse([r for r in plan["changes"] if r["kind"] == "create"])

    def test_missing_profile_reverts_as_create(self):
        drift = {"challenger": [{"field": "profile", "blueprint": "present", "live": "missing (404)"}]}
        plan = compute_plan(self.bp, live_state_from_drift(self.bp, drift), target_instance="x", agent_installed=True, environment="lab")
        self.assertEqual([r["object"] for r in plan["changes"] if r["kind"] == "create"], ["profile challenger"])

    def test_accept_makes_new_valid_version(self):
        new = accept_into_blueprint(self.bp, DRIFT, new_version=4, soul_texts={})
        self.assertEqual(new.metadata.version, 4)
        self.assertIn("quick-lookup", new.agent("sanctions-screener").skills)
        self.assertEqual(new.agent("ownership-tracer").model.name, "llama-4-8b")
        self.assertEqual(new.agent("ownership-tracer").model.provider, "local")
        self.assertEqual(load_blueprint(dump_blueprint(new)).model_dump(), new.model_dump())
        # live state now matches the accepted version: a plan against it has no field changes
        plan = compute_plan(new, live_state_from_drift(self.bp, DRIFT), target_instance="x", agent_installed=True, environment="lab")
        self.assertFalse([r for r in plan["changes"] if r["kind"] in ("create", "update")])

    def test_accept_soul_needs_matching_live_text(self):
        text = "# Objective\n\nEdited on the host.\n"
        import hashlib
        drift = {"challenger": [{"field": "soul_sha256", "blueprint": "sha256:old",
                                 "live": "sha256:" + hashlib.sha256(text.encode()).hexdigest()}]}
        new = accept_into_blueprint(self.bp, drift, new_version=4, soul_texts={"challenger": text})
        self.assertEqual(new.managed_fields()["challenger"]["soul_sha256"], drift["challenger"][0]["live"])
        with self.assertRaises(DriftResolutionError):
            accept_into_blueprint(self.bp, drift, new_version=4, soul_texts={"challenger": "something else"})
        with self.assertRaises(DriftResolutionError):
            accept_into_blueprint(self.bp, drift, new_version=4, soul_texts={})

    def test_accept_refuses_missing_profile(self):
        with self.assertRaises(DriftResolutionError):
            accept_into_blueprint(self.bp, {"challenger": [{"field": "profile", "blueprint": "present", "live": "missing"}]},
                                  new_version=4, soul_texts={})


class DriftStoreTest(unittest.TestCase):
    def setUp(self):
        self.s = Store()
        self.s.create_instance("stg", "staging", "me", "agent")

    def _scan(self, drift):
        job = self.s.enqueue_job("stg", "drift_scan", {}, {"blueprint": "aml-investigation", "version": 3})
        self.s.complete_job(job["id"], {"ok": True, "drift": drift}, instance_id="stg")
        return self.s.drift["stg"]

    def test_report_names_blueprint_version(self):
        report = self._scan(DRIFT)
        self.assertEqual((report["blueprint"], report["version"]), ("aml-investigation", 3))
        self.assertEqual(report["drift"], DRIFT)

    def test_exception_hides_field_until_expiry(self):
        self._scan(DRIFT)
        self.s.add_exceptions("stg", {"sanctions-screener": [SKILLS]}, time.time() + 3600, "me", "vendor hotfix")
        self.assertEqual(self.s.drift["stg"]["drift"], {"ownership-tracer": [MODEL]})
        report = self._scan(DRIFT)  # the next scan still honours it
        self.assertEqual(report["drift"], {"ownership-tracer": [MODEL]})
        self.assertEqual(report["excepted"], {"sanctions-screener": [SKILLS]})
        self.s.drift_exceptions["stg"][0]["expires_at"] = time.time() - 1  # expired: reported again
        self.assertEqual(self._scan(DRIFT)["drift"], DRIFT)

    def test_ignore_once_is_reported_again(self):
        self._scan(DRIFT)
        self.s.move_drift("stg", DRIFT, "ignored")
        self.assertEqual(self.s.drift["stg"]["drift"], {})
        self.assertEqual(self._scan(DRIFT)["drift"], DRIFT)


if __name__ == "__main__":
    unittest.main()
