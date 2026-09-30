"""goAML reports (Egypt: EMLCU): a decided Decision Room's SAR draft, as the XML the FIU's goAML portal accepts.

goAML is UNODC's reporting system; Egypt's FIU (EMLCU) has required suspicious transaction reports through it since
2022. Every FIU publishes its own XSD: the element names and their order are the UNODC standard (the tables below;
checked against a published FIU schema), while code lists, required fields and business rules (XSD 1.1 asserts)
are the FIU's own. So Fleet Control builds the report in the standard order and **the FIU's XSD decides**: the bank
downloads it from its goAML portal and loads it (``validate``); a report is ready to file only when it passes.

Fleet Control never files. A person (the MLRO) prepares the report from a room whose decision is in, is recorded
as its reporting person, downloads the XML, uploads it to goAML and records the FIU's reference back. No goAML
credential ever reaches Fleet Control.

The draft an agent writes (``fleetcontrol.goaml-draft/v1``) uses goAML's own element names, so the mapping is
mechanical and the FIU's error messages name the same fields the agent wrote::

    {"schema": "fleetcontrol.goaml-draft/v1", "report_code": "STR", "reason": "...", "action": "...",
     "indicators": ["..."], "transactions": [{"transactionnumber": "...", "t_from_my_client": {...}, "t_to": {...}}]}

A report without transactions (``"parties": [{"person": {...}, "significance": 5}]``) becomes an activity report.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Optional

DRAFT_SCHEMA = "fleetcontrol.goaml-draft/v1"

# The UNODC goAML v4 element order of each type. A published FIU schema (Sri Lanka FIU, v4-based) has exactly these
# sequences; an FIU that drops or adds an element rejects it by name when its XSD validates the report.
_PERSON = ["gender", "title", "first_name", "middle_name", "prefix", "last_name", "birthdate", "birth_place",
           "mothers_name", "alias", "ssn", "passport_number", "passport_country", "id_number", "nationality1",
           "nationality2", "nationality3", "residence", "phones", "addresses", "email", "occupation", "employer_name",
           "employer_address_id", "employer_phone_id", "identification", "deceased", "date_deceased", "tax_number",
           "tax_reg_number", "source_of_wealth", "comments"]
ORDER: dict[str, list[str]] = {
    "report": ["rentity_id", "rentity_branch", "submission_code", "report_code", "entity_reference", "fiu_ref_number",
               "submission_date", "currency_code_local", "reporting_person", "location", "reason", "action",
               "transaction", "activity", "report_indicators"],
    "transaction": ["transactionnumber", "internal_ref_number", "transaction_location", "transaction_description",
                    "date_transaction", "teller", "authorized", "late_deposit", "date_posting", "value_date",
                    "transmode_code", "transmode_comment", "amount_local", "involved_parties", "t_from_my_client",
                    "t_from", "t_to_my_client", "t_to", "goods_services", "comments"],
    "t_from": ["from_funds_code", "from_funds_comment", "from_foreign_currency", "t_conductor", "from_account",
               "from_person", "from_entity", "from_country"],
    "t_to": ["to_funds_code", "to_funds_comment", "to_foreign_currency", "to_account", "to_person", "to_entity",
             "to_country"],
    "t_account": ["institution_name", "institution_code", "swift", "non_bank_institution", "branch", "account",
                  "currency_code", "account_name", "iban", "client_number", "personal_account_type", "t_entity",
                  "signatory", "opened", "closed", "balance", "date_balance", "status_code", "beneficiary",
                  "beneficiary_comment", "comments"],
    "t_person": _PERSON,
    "director": _PERSON + ["role"],
    "t_entity": ["name", "commercial_name", "incorporation_legal_form", "incorporation_number", "business", "phones",
                 "addresses", "email", "url", "incorporation_state", "incorporation_country_code", "director_id",
                 "incorporation_date", "business_closed", "date_business_closed", "tax_number", "tax_reg_number",
                 "comments"],
    "t_address": ["address_type", "address", "town", "city", "zip", "country_code", "state", "comments"],
    "t_phone": ["tph_contact_type", "tph_communication_type", "tph_country_prefix", "tph_number", "tph_extension",
                "comments"],
    "t_foreign_currency": ["foreign_currency_code", "foreign_amount", "foreign_exchange_rate"],
    "t_person_identification": ["type", "number", "issue_date", "expiry_date", "issued_by", "issue_country", "comments"],
    "signatory": ["is_primary", "t_person", "role"],
    "report_party": ["person", "account", "entity", "significance", "reason", "comments"],
}
# which type an element has, wherever it appears
TYPE_OF: dict[str, str] = {
    "t_from_my_client": "t_from", "t_from": "t_from", "t_to_my_client": "t_to", "t_to": "t_to",
    "from_account": "t_account", "to_account": "t_account", "account": "t_account",
    "from_person": "t_person", "to_person": "t_person", "t_conductor": "t_person", "person": "t_person",
    "t_person": "t_person", "reporting_person": "t_person",
    "from_entity": "t_entity", "to_entity": "t_entity", "entity": "t_entity", "t_entity": "t_entity",
    "director_id": "director",
    "from_foreign_currency": "t_foreign_currency", "to_foreign_currency": "t_foreign_currency",
    "address": "t_address", "location": "t_address", "employer_address_id": "t_address",
    "phone": "t_phone", "employer_phone_id": "t_phone",
    "identification": "t_person_identification", "signatory": "signatory", "report_party": "report_party",
    "transaction": "transaction",
}
# a list in the draft becomes a wrapper element with one child per item
WRAPPERS = {"phones": "phone", "addresses": "address"}


class GoamlError(ValueError):
    pass


def _text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}" if value != int(value) else f"{int(value)}.00"
    return str(value)


def _fill(parent: ET.Element, type_name: str, data: dict, path: str, problems: list[str]) -> None:
    order = ORDER[type_name]
    for key in data:
        if key not in order:
            problems.append(f"{path}.{key}: not a goAML element of {type_name}")
    for name in order:
        value = data.get(name)
        if value is None or value == "" or value == []:
            continue
        here = f"{path}.{name}"
        if name in WRAPPERS:
            wrapper = ET.SubElement(parent, name)
            for i, item in enumerate(value if isinstance(value, list) else [value]):
                _child(wrapper, WRAPPERS[name], item, f"{here}[{i}]", problems)
            continue
        for i, item in enumerate(value if isinstance(value, list) else [value]):
            _child(parent, name, item, f"{here}[{i}]" if isinstance(value, list) else here, problems)


def _child(parent: ET.Element, name: str, value: Any, path: str, problems: list[str]) -> None:
    el = ET.SubElement(parent, name)
    if isinstance(value, dict):
        type_name = TYPE_OF.get(name)
        if not type_name:
            problems.append(f"{path}: {name} holds a value, not a group of fields")
            return
        _fill(el, type_name, value, path, problems)
    else:
        el.text = _text(value)


def check_draft(draft: Any) -> list[str]:
    """What is wrong with a draft before any XML is made. The FIU's XSD checks the rest."""
    if not isinstance(draft, dict):
        return ["the draft is not a JSON object"]
    problems = []
    if draft.get("schema") != DRAFT_SCHEMA:
        problems.append(f"schema must be {DRAFT_SCHEMA!r}")
    if not str(draft.get("report_code") or "").strip():
        problems.append("report_code is missing (the FIU's code, e.g. STR)")
    if not str(draft.get("reason") or "").strip():
        problems.append("reason is missing: why the report is made")
    if not draft.get("indicators"):
        problems.append("indicators is missing: at least one of the FIU's report indicators")
    txs, parties = draft.get("transactions") or [], draft.get("parties") or []
    if bool(txs) == bool(parties):
        problems.append("give transactions (a transaction report) or parties (an activity report), not both and not neither")
    for i, t in enumerate(txs):
        if not isinstance(t, dict):
            problems.append(f"transactions[{i}] is not an object")
            continue
        for side in ("from", "to"):
            present = [k for k in (f"t_{side}_my_client", f"t_{side}") if t.get(k)]
            if len(present) != 1:
                problems.append(f"transactions[{i}]: give exactly one of t_{side}_my_client and t_{side}")
    known = {"schema", "report_code", "reason", "action", "indicators", "transactions", "parties", "fiu_ref_number"}
    problems += [f"{k}: not part of a goAML draft" for k in draft if k not in known]
    return problems


def build_xml(draft: dict, *, profile: dict, reporter: dict, entity_reference: str,
              submitted_at: Optional[float] = None) -> tuple[bytes, list[str]]:
    """The report XML and any problem found while building it. ``profile`` is the bank's goAML settings (rentity_id,
    rentity_branch, currency_code_local, submission_code, location); ``reporter`` the person who prepares it."""
    problems = check_draft(draft)
    if not profile.get("rentity_id"):
        problems.append("the bank's goAML reporting-entity id is not set (Settings → goAML)")
    root = ET.Element("report")
    at = datetime.fromtimestamp(submitted_at if submitted_at is not None else datetime.now(timezone.utc).timestamp(), timezone.utc)
    header = {
        "rentity_id": profile.get("rentity_id"), "rentity_branch": profile.get("rentity_branch"),
        "submission_code": profile.get("submission_code") or "E", "report_code": draft.get("report_code"),
        "entity_reference": entity_reference, "fiu_ref_number": draft.get("fiu_ref_number"),
        "submission_date": at.strftime("%Y-%m-%dT%H:%M:%S"), "currency_code_local": profile.get("currency_code_local") or "EGP",
        "reporting_person": reporter or None, "location": profile.get("location") or None,
        "reason": draft.get("reason"), "action": draft.get("action"),
    }
    for name in ORDER["report"][:12]:
        if header.get(name) not in (None, "", {}):
            _child(root, name, header[name], name, problems)
    for i, tx in enumerate(draft.get("transactions") or []):
        if isinstance(tx, dict):
            _child(root, "transaction", tx, f"transactions[{i}]", problems)
    if draft.get("parties"):
        parties = ET.SubElement(ET.SubElement(root, "activity"), "report_parties")
        for i, p in enumerate(draft["parties"]):
            if isinstance(p, dict):
                _child(parties, "report_party", p, f"parties[{i}]", problems)
    indicators = ET.SubElement(root, "report_indicators")
    for code in draft.get("indicators") or []:
        ET.SubElement(indicators, "indicator").text = _text(code)
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), problems


_SCHEMAS: dict[str, Any] = {}


def load_schema(xsd: str):
    """The FIU's XSD, compiled (XSD 1.1: goAML schemas carry asserts). Raises GoamlError when it is not a goAML
    schema. Compiled schemas are kept by content hash."""
    key = hashlib.sha256(xsd.encode("utf-8")).hexdigest()
    if key in _SCHEMAS:
        return _SCHEMAS[key]
    try:
        import xmlschema
    except ImportError as exc:  # pragma: no cover - a dependency of the API package
        raise GoamlError("the xmlschema package is not installed") from exc
    try:
        schema = xmlschema.XMLSchema11(xsd)
    except Exception as exc:
        raise GoamlError(f"not a valid XSD: {str(exc).splitlines()[0][:300]}") from exc
    if "report" not in schema.elements:
        raise GoamlError("this XSD has no <report> element: it is not a goAML reporting schema")
    _SCHEMAS[key] = schema
    return schema


def validate(xml: bytes, xsd: str, limit: int = 50) -> list[str]:
    """Every way the report breaks the FIU's schema, as "where: what", at most ``limit``."""
    schema = load_schema(xsd)
    errors = []
    for err in schema.iter_errors(xml.decode("utf-8")):
        where = err.path or "/report"
        errors.append(f"{where}: {(err.reason or str(err)).splitlines()[0][:300]}")
        if len(errors) >= limit:
            break
    return errors


def schema_sha(xsd: str) -> str:
    return hashlib.sha256(xsd.encode("utf-8")).hexdigest()
