import os
import unittest

from fleetcontrol_blueprint import load_blueprint
from fleetcontrol_api.edits import EditError, as_new_version, update_agent

EXAMPLE = os.path.join(os.path.dirname(__file__), "..", "..", "..", "packages", "blueprint_schema", "examples", "aml-investigation.yaml")


class AgentEditTest(unittest.TestCase):
    def setUp(self):
        self.bp = load_blueprint(EXAMPLE)

    def test_edit_changes_managed_fields(self):
        new, changed = update_agent(self.bp, "challenger", {"skills": ["counter-argument", "citation-check"], "role": "Argues the counter-case."})
        self.assertEqual(sorted(changed), ["role", "skills"])
        mf = new.managed_fields()["challenger"]
        self.assertEqual(mf["skills"], ["citation-check", "counter-argument"])
        self.assertEqual(mf["description"], "Argues the counter-case.")
        self.assertEqual(new.agent("sanctions-screener").model_dump(), self.bp.agent("sanctions-screener").model_dump())

    def test_unchanged_values_are_not_reported(self):
        same = self.bp.agent("challenger").model_dump(mode="json", exclude_none=True)["skills"]
        self.assertEqual(update_agent(self.bp, "challenger", {"skills": same})[1], [])

    def test_soul_edit_changes_hash(self):
        soul = self.bp.agent("challenger").soul.model_dump(mode="json", exclude_none=True)
        soul["objective"] = "Find the strongest benign explanation."
        new, changed = update_agent(self.bp, "challenger", {"soul": soul})
        self.assertEqual(changed, ["soul"])
        self.assertNotEqual(new.managed_fields()["challenger"]["soul_sha256"], self.bp.managed_fields()["challenger"]["soul_sha256"])

    def test_rejections(self):
        with self.assertRaises(EditError):
            update_agent(self.bp, "challenger", {"id": "renamed"})
        with self.assertRaises(KeyError):
            update_agent(self.bp, "ghost", {"role": "x"})
        with self.assertRaises(ValueError):  # cross-reference validation still runs
            update_agent(self.bp, "challenger", {"delegates_to": ["ghost"]})
        with self.assertRaises(ValueError):
            update_agent(self.bp, "challenger", {"model": {"provider": "openai"}})  # name missing

    def test_new_version(self):
        self.assertEqual(as_new_version(self.bp, 4).metadata.version, 4)


if __name__ == "__main__":
    unittest.main()
