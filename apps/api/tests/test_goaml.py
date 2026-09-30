"""goAML: a Decision Room's SAR draft as the XML an FIU's goAML portal accepts, checked against the FIU's schema."""

import copy
import os
import unittest
import xml.etree.ElementTree as ET

try:
    import xmlschema  # noqa: F401
except ImportError:  # pragma: no cover
    xmlschema = None

from fleetcontrol_api.goaml import DRAFT_SCHEMA, ORDER, GoamlError, build_xml, check_draft, load_schema, validate

XSD = open(os.path.join(os.path.dirname(__file__), "fixtures", "goaml-test.xsd"), encoding="utf-8").read()
ADDRESS = {"address_type": "B", "address": "12 Tahrir St", "city": "Cairo", "country_code": "EG"}
PROFILE = {"rentity_id": 1042, "currency_code_local": "EGP", "submission_code": "E", "location": ADDRESS}
REPORTER = {"first_name": "Mona", "last_name": "Hassan", "email": "mlro@bank.example", "occupation": "MLRO"}
DRAFT = {
    "schema": DRAFT_SCHEMA, "report_code": "STR",
    "reason": "Eleven cash deposits just under the EGP 200,000 threshold in nine days, then a wire to Limassol.",
    "action": "Account placed under enhanced monitoring; relationship under review.",
    "indicators": ["STR-CASH"],
    "transactions": [{
        "transactionnumber": "TX-2026-0412-1", "transaction_description": "Cash deposit at branch",
        "date_transaction": "2026-08-30T10:15:00", "transmode_code": "A", "amount_local": 190000,
        "t_from": {"from_funds_code": "K", "from_person": {"first_name": "Omar", "last_name": "Fahmy"}, "from_country": "EG"},
        "t_to_my_client": {"to_funds_code": "K", "to_country": "EG",
                           "to_account": {"institution_name": "Bank of Example", "swift": "EXAMEGCX", "account": "1002003004",
                                          "currency_code": "EGP", "opened": "2019-01-10T00:00:00", "balance": 12500.5}},
    }],
}


def build(draft=None, **kw):
    return build_xml(copy.deepcopy(draft or DRAFT), profile=kw.get("profile", PROFILE), reporter=REPORTER,
                     entity_reference="FC-TEST-1", submitted_at=1_790_000_000)


class BuildTest(unittest.TestCase):
    def test_the_report_follows_the_goaml_element_order(self):
        xml, problems = build()
        self.assertEqual(problems, [])
        root = ET.fromstring(xml)
        self.assertEqual([c.tag for c in root], ["rentity_id", "submission_code", "report_code", "entity_reference", "submission_date",
                                                 "currency_code_local", "reporting_person", "location", "reason", "action",
                                                 "transaction", "report_indicators"])
        tx = root.find("transaction")
        self.assertEqual([c.tag for c in tx], ["transactionnumber", "transaction_description", "date_transaction", "transmode_code",
                                               "amount_local", "t_from", "t_to_my_client"])
        self.assertEqual(root.findtext("submission_date"), "2026-09-21T14:13:20")
        self.assertEqual(tx.findtext("t_to_my_client/to_account/balance"), "12500.50")
        self.assertEqual(root.findtext("reporting_person/email"), "mlro@bank.example")  # the person, from their account

    def test_lists_become_wrappers_and_repeated_elements(self):
        draft = copy.deepcopy(DRAFT)
        draft["transactions"][0]["t_from"]["from_person"].update(
            phones=[{"tph_contact_type": "P", "tph_communication_type": "M", "tph_number": "01001234567"}], addresses=[ADDRESS, ADDRESS])
        root = ET.fromstring(build(draft)[0])
        person = root.find("transaction/t_from/from_person")
        self.assertEqual(len(person.findall("addresses/address")), 2)
        self.assertEqual(person.findtext("phones/phone/tph_number"), "01001234567")

    def test_an_activity_report_lists_its_parties(self):
        draft = {**copy.deepcopy(DRAFT), "report_code": "SAR", "transactions": [],
                 "parties": [{"person": {"first_name": "Omar", "last_name": "Fahmy"}, "significance": 8, "reason": "Account holder"}]}
        root = ET.fromstring(build(draft)[0])
        self.assertEqual(root.findtext("activity/report_parties/report_party/person/last_name"), "Fahmy")
        self.assertIsNone(root.find("transaction"))

    def test_what_a_draft_gets_wrong_is_said_before_any_schema(self):
        self.assertEqual(check_draft(DRAFT), [])
        bad = copy.deepcopy(DRAFT)
        bad["transactions"][0]["t_to"] = {"to_funds_code": "K", "to_country": "EG"}  # both t_to and t_to_my_client
        del bad["indicators"]
        bad["verdict"] = "file it"
        problems = check_draft(bad)
        self.assertTrue(any("exactly one of t_to_my_client and t_to" in p for p in problems))
        self.assertTrue(any("indicators is missing" in p for p in problems))
        self.assertTrue(any(p.startswith("verdict:") for p in problems))
        self.assertIn("the draft is not a JSON object", check_draft(["x"]))

    def test_a_field_goaml_does_not_have_is_named_with_its_path(self):
        draft = copy.deepcopy(DRAFT)
        draft["transactions"][0]["t_from"]["from_person"]["favourite_colour"] = "blue"
        _, problems = build(draft)
        self.assertIn("transactions[0].t_from.from_person.favourite_colour: not a goAML element of t_person", problems)

    def test_no_reporting_entity_id_no_report(self):
        _, problems = build(profile={"currency_code_local": "EGP"})
        self.assertTrue(any("reporting-entity id" in p for p in problems))

    def test_every_type_the_builder_names_has_an_order(self):
        from fleetcontrol_api.goaml import TYPE_OF
        self.assertTrue(set(TYPE_OF.values()) <= set(ORDER))


@unittest.skipIf(xmlschema is None, "xmlschema not installed")
class SchemaTest(unittest.TestCase):
    def test_a_good_report_passes_the_fiu_schema(self):
        xml, _ = build()
        self.assertEqual(validate(xml, XSD), [])

    def test_the_schema_names_each_break_by_where_it_is(self):
        draft = copy.deepcopy(DRAFT)
        draft["indicators"] = ["NOT-A-CODE"]
        draft["transactions"][0]["transmode_code"] = "Z"
        errors = validate(build(draft)[0], XSD)
        self.assertTrue(any(e.startswith("/report/report_indicators/indicator:") for e in errors), errors)
        self.assertTrue(any("transmode_code" in e for e in errors), errors)

    def test_the_fiu_business_rules_are_checked_too(self):
        # an STR with no transaction breaks the schema's XSD 1.1 assert
        draft = {**copy.deepcopy(DRAFT), "transactions": [], "parties": [{"person": {"first_name": "A", "last_name": "B"}}]}
        errors = validate(build(draft)[0], XSD)
        self.assertTrue(errors)

    def test_only_a_goaml_schema_is_accepted(self):
        with self.assertRaises(GoamlError):
            load_schema("<nonsense/>")
        with self.assertRaises(GoamlError):
            load_schema('<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"><xs:element name="invoice" type="xs:string"/></xs:schema>')


if __name__ == "__main__":
    unittest.main()
