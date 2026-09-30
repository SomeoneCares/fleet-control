"""goAML through FastAPI: the bank's profile and the FIU schema, a report prepared from a decided room, filing."""

import json
import os
import unittest

try:
    from fastapi.testclient import TestClient

    from fleetcontrol_api.main import store
    from tests.helpers import signed_in
    from tests.test_goaml import DRAFT, XSD
except ImportError:  # pragma: no cover
    TestClient = None

PROFILE = {"fiu": "EMLCU (Egypt)", "rentity_id": 1042, "currency_code_local": "EGP", "submission_code": "E",
           "location": {"address_type": "B", "address": "12 Tahrir St", "city": "Cairo", "country_code": "EG"}}
REPORTER = {"first_name": "Mona", "last_name": "Hassan", "occupation": "MLRO"}


@unittest.skipIf(TestClient is None, "fastapi/httpx not installed")
class GoamlApiTest(unittest.TestCase):
    def setUp(self):
        self.admin = signed_in("admin")
        self.approver = signed_in("approver")
        self.zone = "goaml-" + os.urandom(3).hex()
        self.admin.post("/api/v1/content/zones", json={"id": self.zone, "name": "SAR cases", "read_roles": ["approver"]})
        self.addCleanup(lambda: store.delete_zone(self.zone))
        self.assertEqual(self.admin.put("/api/v1/goaml/settings", json=PROFILE).status_code, 200)
        self.room = self.admin.post("/api/v1/rooms", json={"question": "Should we file an STR for Alpha Trading LLC?", "zone": self.zone,
                                                           "options": ["File with EMLCU", "Do not file"], "case": "AML-2026-0412"}).json()
        self.draft = self.admin.post("/api/v1/outputs", json={"zone": self.zone, "name": "str-draft.json", "kind": "structured",
                                                              "text": "Here is the draft:\n```json\n" + json.dumps(DRAFT) + "\n```"}).json()
        self.admin.post(f"/api/v1/rooms/{self.room['id']}/evidence", json={"kind": "output", "ref": self.draft["id"], "label": "STR draft"})

    def decide(self):
        r = self.approver.post(f"/api/v1/rooms/{self.room['id']}/decide",
                               json={"option": self.room["options"][0]["id"], "rationale": "Structuring below the threshold, then offshore."})
        self.assertEqual(r.status_code, 200, r.text)

    def prepare(self, client=None):
        return (client or self.approver).post("/api/v1/goaml/reports", json={"room_id": self.room["id"], "output_id": self.draft["id"],
                                                                             "reporter": REPORTER})

    def load_schema(self):
        r = self.admin.post("/api/v1/goaml/schema", json={"name": "goaml-test.xsd", "xsd": XSD})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertNotIn("xsd", r.json())  # the schema text stays in the database
        return r.json()

    def test_nothing_is_prepared_before_the_people_decide(self):
        seen = self.approver.get(f"/api/v1/goaml/rooms/{self.room['id']}").json()
        self.assertEqual(([d["output_id"] for d in seen["drafts"]], seen["may_prepare"]), ([self.draft["id"]], False))
        self.assertIn("decision", seen["why"])
        self.assertEqual(self.prepare().status_code, 409)

    def test_a_report_is_prepared_checked_downloaded_and_recorded_as_filed(self):
        self.load_schema()
        self.decide()
        r = self.prepare()
        self.assertEqual(r.status_code, 201, r.text)
        report = r.json()
        self.assertEqual((report["status"], report["errors"], report["problems"]), ("ready", [], []), report)
        self.assertEqual((report["reporter"]["email"], report["schema"]["name"]), (report["prepared_by"], "goaml-test.xsd"))
        self.assertEqual(report["decision"]["option"]["label"], "File with EMLCU")
        xml = self.approver.get(f"/api/v1/goaml/reports/{report['id']}/xml")
        self.assertEqual((xml.status_code, xml.headers["content-type"].split(";")[0]), (200, "application/xml"))
        self.assertIn(report["entity_reference"], xml.headers["content-disposition"])
        self.assertIn(b"<rentity_id>1042</rentity_id>", xml.content)

        self.assertEqual(self.approver.post(f"/api/v1/goaml/reports/{report['id']}/filed", json={"fiu_ref_number": " "}).status_code, 422)
        filed = self.approver.post(f"/api/v1/goaml/reports/{report['id']}/filed", json={"fiu_ref_number": "EMLCU-2026-00913"})
        self.assertEqual((filed.status_code, filed.json()["status"], filed.json()["filed"]["fiu_ref_number"]), (200, "filed", "EMLCU-2026-00913"))
        self.assertEqual(self.approver.post(f"/api/v1/goaml/reports/{report['id']}/filed", json={"fiu_ref_number": "again"}).status_code, 409)
        actions = {e["action"] for e in self.admin.get("/api/v1/audit", params={"limit": 2000}).json() if e["target"] == report["id"]}
        self.assertTrue({"goaml.prepared", "goaml.downloaded", "goaml.filed"} <= actions, actions)

    def test_a_report_that_breaks_the_fiu_schema_cannot_be_filed(self):
        self.load_schema()
        bad = {**DRAFT, "indicators": ["NOT-A-CODE"]}
        other = self.admin.post("/api/v1/outputs", json={"zone": self.zone, "name": "str-draft-2.json", "kind": "structured",
                                                         "text": json.dumps(bad)}).json()
        self.admin.post(f"/api/v1/rooms/{self.room['id']}/evidence", json={"kind": "output", "ref": other["id"], "label": "STR draft 2"})
        self.decide()
        r = self.approver.post("/api/v1/goaml/reports", json={"room_id": self.room["id"], "output_id": other["id"], "reporter": REPORTER}).json()
        self.assertEqual(r["status"], "invalid")
        self.assertTrue(any("indicator" in e for e in r["errors"]), r["errors"])
        refused = self.approver.post(f"/api/v1/goaml/reports/{r['id']}/filed", json={"fiu_ref_number": "X-1"})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("passed the FIU's schema", refused.json()["detail"])
        self.assertTrue(self.approver.get(f"/api/v1/goaml/reports/{r['id']}/xml").headers["content-disposition"].endswith('-NOT-VALID.xml"'))

    def test_without_the_fiu_schema_a_report_is_unchecked_until_one_is_loaded(self):
        self.decide()
        from unittest import mock
        with mock.patch.object(store, "goaml_schema", return_value=None):
            report = self.prepare().json()
        self.assertEqual(report["status"], "unchecked")
        self.assertEqual(self.approver.post(f"/api/v1/goaml/reports/{report['id']}/filed", json={"fiu_ref_number": "X"}).status_code, 409)
        self.load_schema()
        rechecked = self.approver.post(f"/api/v1/goaml/reports/{report['id']}/check").json()
        self.assertEqual(rechecked["status"], "ready")

    def test_who_may_do_what(self):
        self.decide()
        for role in ("operator", "fleet_architect", "viewer"):
            c = signed_in(role)
            self.assertEqual(c.get("/api/v1/goaml/reports").status_code, 403, role)
            self.assertEqual(self.prepare(c).status_code, 403, role)
        self.assertEqual(self.approver.put("/api/v1/goaml/settings", json=PROFILE).status_code, 403)  # Admin setting
        self.assertEqual(self.approver.post("/api/v1/goaml/schema", json={"name": "x.xsd", "xsd": XSD}).status_code, 403)
        self.assertEqual(self.admin.post("/api/v1/goaml/schema", json={"name": "x.xsd", "xsd": "<nonsense>" + " " * 100 + "</nonsense>"}).status_code, 422)
        # someone who cannot read the room's zone does not see its reports
        report = self.prepare().json()
        outsider_zone = self.zone + "-other"
        self.admin.post("/api/v1/content/zones", json={"id": outsider_zone, "name": "Other", "read_roles": []})
        self.addCleanup(lambda: store.delete_zone(outsider_zone))
        store.update_goaml_report(report["id"], lambda d: d.update(zone=outsider_zone))
        self.assertEqual(self.approver.get(f"/api/v1/goaml/reports/{report['id']}").status_code, 404)
        self.assertNotIn(report["id"], [r["id"] for r in self.approver.get("/api/v1/goaml/reports").json()])


if __name__ == "__main__":
    unittest.main()
