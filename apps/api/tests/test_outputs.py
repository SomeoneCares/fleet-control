"""Fleet outputs: what an output must carry, and how its provenance reads."""

import unittest

from fleetcontrol_api.content import ContentError
from fleetcontrol_api.outputs import KINDS, OutputError, new_output, output_row, provenance


def make(**kw):
    base = {"output_id": "out_1", "zone": "case-files", "name": " SAR draft v1.docx ", "kind": "document",
            "classification": "restricted", "produced_by": "sar-drafter", "at": 10.0, "text": "draft",
            "case": "AML-2026-0412", "source": {"kind": "agent", "ref": "run_9"}, "instance_id": "prod-01"}
    base.update(kw)
    return new_output(**base)


class OutputTest(unittest.TestCase):
    def test_an_output_carries_zone_class_and_provenance(self):
        o = make()
        self.assertEqual((o["name"], o["size"], o["case"]), ("SAR draft v1.docx", 5, "AML-2026-0412"))
        self.assertEqual(provenance(o), "sar-drafter produced it from an agent run (run_9) on prod-01")
        self.assertEqual(provenance(make(source={"kind": "ask"}, instance_id=None, produced_by="marcus@example.org")),
                         "marcus@example.org produced it from a question to the fleet")

    def test_the_row_hides_the_text_unless_it_is_asked_for(self):
        o = make()
        self.assertNotIn("text", output_row(o))
        self.assertEqual(output_row(o, with_text=True)["text"], "draft")
        self.assertEqual(set(output_row(o)), {"id", "zone", "name", "kind", "classification", "produced_by", "case", "blueprint",
                                              "instance_id", "source", "size", "at"})

    def test_what_an_output_may_not_be(self):
        for bad in ({"name": " "}, {"kind": "video"}, {"produced_by": ""}, {"source": {"kind": "guess"}}, {"text": "x" * 200_001}):
            with self.assertRaises(OutputError, msg=str(bad)):
                make(**bad)
        with self.assertRaises(ContentError):
            make(classification="secret")
        self.assertEqual(KINDS[0], "document")


if __name__ == "__main__":
    unittest.main()
