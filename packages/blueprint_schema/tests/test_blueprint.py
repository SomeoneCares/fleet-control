"""Blueprint schema tests. Runnable with either pytest or `python -m unittest`."""

import os
import unittest

from fleetcontrol_blueprint import (
    Blueprint,
    dump_blueprint,
    json_schema,
    load_blueprint,
)

HERE = os.path.dirname(__file__)
EXAMPLE = os.path.join(HERE, "..", "examples", "aml-investigation.yaml")


class ExampleBlueprint(unittest.TestCase):
    def setUp(self):
        self.bp = load_blueprint(EXAMPLE)

    def test_loads(self):
        self.assertEqual(self.bp.metadata.name, "aml-investigation")
        self.assertEqual(len(self.bp.agents), 5)
        self.assertEqual(self.bp.requires.agent, "required")

    def test_production_target_defaults_to_two_approvals(self):
        prod = [t for t in self.bp.targets if t.environment == "production"][0]
        self.assertEqual(prod.requires_approvals, 2)

    def test_round_trip_is_stable(self):
        text = dump_blueprint(self.bp)
        again = load_blueprint(text)
        self.assertEqual(again.model_dump(), self.bp.model_dump())
        self.assertEqual(dump_blueprint(again), text)

    def test_managed_fields_have_soul_hash(self):
        mf = self.bp.managed_fields()
        self.assertIn("sanctions-screener", mf)
        self.assertTrue(mf["sanctions-screener"]["soul_sha256"].startswith("sha256:"))
        self.assertEqual(mf["sanctions-screener"]["skills"], ["list-versioning", "name-matching"])

    def test_soul_render_is_deterministic(self):
        a = self.bp.agent("sanctions-screener")
        self.assertEqual(a.soul.render(), a.soul.render())
        self.assertIn("# Boundaries", a.soul.render())

    def test_json_schema_exports(self):
        schema = json_schema()
        self.assertEqual(schema["title"], "Blueprint")
        self.assertIn("agents", schema["properties"])


class Validation(unittest.TestCase):
    def _minimal(self, **overrides):
        data = {
            "metadata": {"name": "test-fleet", "version": 1, "owner": "me"},
            "mission": "m",
            "agents": [
                {
                    "id": "alpha",
                    "role": "r",
                    "model": {"provider": "local", "name": "llama"},
                    "soul": {"objective": "o"},
                }
            ],
        }
        data.update(overrides)
        return data

    def test_unknown_key_rejected(self):
        with self.assertRaises(Exception):
            Blueprint.model_validate(self._minimal(bogus=1))

    def test_unknown_delegate_rejected(self):
        d = self._minimal()
        d["agents"][0]["delegates_to"] = ["ghost"]
        with self.assertRaises(Exception) as cm:
            Blueprint.model_validate(d)
        self.assertIn("unknown agent 'ghost'", str(cm.exception))

    def test_test_target_must_exist(self):
        d = self._minimal(tests=[{"id": "x", "target": "nope", "scenario": "s"}])
        with self.assertRaises(Exception):
            Blueprint.model_validate(d)

    def test_secret_values_rejected(self):
        d = self._minimal(secrets=[{"name": "k", "ref": "sk-live-123"}])
        with self.assertRaises(Exception):
            Blueprint.model_validate(d)

    def test_bad_id_rejected(self):
        d = self._minimal()
        d["agents"][0]["id"] = "Not Valid"
        with self.assertRaises(Exception):
            Blueprint.model_validate(d)

    def test_raw_soul_wins(self):
        d = self._minimal()
        d["agents"][0]["soul"] = {"objective": "ignored", "raw": "custom soul"}
        bp = Blueprint.model_validate(d)
        self.assertEqual(bp.agents[0].soul.render(), "custom soul\n")


if __name__ == "__main__":
    unittest.main()
