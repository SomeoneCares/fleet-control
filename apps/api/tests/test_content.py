"""Content zones: who may read one, which agents read it, and what a file may be."""

import unittest

from fleetcontrol_api.content import (
    CLASSIFICATIONS, ContentError, agent_access, check_classification, file_row, may_read, new_file, new_zone, readable_zones, zone_id,
)

BLUEPRINT = {
    "metadata": {"name": "aml"},
    "policies": [{"id": "data-residency", "kind": "data-residency", "enforcement": "block", "applies_to": ["*"]}],
    "agents": [
        {"id": "case-orchestrator", "content_zones": ["case-files"], "model": {"provider": "anthropic", "name": "claude", "data_class": "redacted-only"}},
        {"id": "screener", "content_zones": ["case-files", "sanctions-lists"], "model": {"provider": "local", "name": "llama", "data_class": "raw"}},
        {"id": "drafter", "content_zones": [], "model": {"provider": "local", "name": "llama", "data_class": "raw"}},
    ],
}


class ZoneTest(unittest.TestCase):
    def setUp(self):
        self.zone = new_zone(id="Case files", name="Case files", description=" investigation material ",
                             read_roles=["approver", "approver", "viewer"], by="dana@example.org", at=1.0)

    def test_a_zone_is_made_from_plain_words(self):
        self.assertEqual((self.zone["id"], self.zone["name"], self.zone["description"]), ("case-files", "Case files", "investigation material"))
        self.assertEqual(self.zone["read_roles"], ["approver", "viewer"])  # deduplicated and sorted
        self.assertFalse(self.zone["managed"])
        for bad in ("", "9lives", "x"):
            with self.assertRaises(ContentError, msg=bad):
                zone_id(bad)

    def test_roles_reach_a_zone_and_admins_always_do(self):
        self.assertTrue(may_read(self.zone, "approver"))
        self.assertTrue(may_read(self.zone, "admin"))
        self.assertFalse(may_read(self.zone, "operator"))
        other = new_zone(id="drafts", name="Shared drafts", by="x", at=1.0)
        self.assertEqual(readable_zones([self.zone, other], "viewer"), ["case-files"])
        self.assertEqual(readable_zones([self.zone, other], "admin"), ["case-files", "drafts"])
        self.assertEqual(readable_zones([self.zone, other], "operator"), [])

    def test_agents_reach_a_zone_through_the_blueprint(self):
        rows = agent_access("case-files", [BLUEPRINT])
        self.assertEqual([(r["agent"], r["redacted_only"]) for r in rows], [("case-orchestrator", True), ("screener", False)])
        self.assertEqual(agent_access("nothing", [BLUEPRINT]), [])


class FileTest(unittest.TestCase):
    def test_a_file_carries_its_classification_and_size(self):
        f = new_file(zone="case-files", name=" wire log.csv ", classification="restricted", text="a,b\n1,2\n",
                     by="dana@example.org", at=2.0, file_id="cf_1")
        self.assertEqual((f["name"], f["classification"], f["size"]), ("wire log.csv", "restricted", 8))
        self.assertEqual(set(file_row(f)), {"id", "zone", "name", "classification", "size", "uploaded_by", "at"})
        self.assertEqual(file_row(f, with_text=True)["text"], "a,b\n1,2\n")

    def test_what_a_file_may_not_be(self):
        with self.assertRaises(ContentError):
            new_file(zone="z", name="  ", classification="internal", text=None, by="x", at=1.0, file_id="cf_2")
        with self.assertRaises(ContentError):
            new_file(zone="z", name="big.txt", classification="internal", text="x" * 200_001, by="x", at=1.0, file_id="cf_3")
        with self.assertRaises(ContentError):
            check_classification("secret")
        self.assertEqual(CLASSIFICATIONS, ("internal", "confidential", "restricted"))


if __name__ == "__main__":
    unittest.main()
