# goAML reports (Egypt: EMLCU)

Egypt's FIU, the Egyptian Money Laundering and Terrorist Financing Combating Unit (EMLCU), has required
suspicious transaction reports through **goAML** since 2022: UNODC's reporting system, where each report is an XML
file that must validate against the FIU's own XSD. Fleet Control prepares those files. **It never files one.**

## What Fleet Control does, and what it leaves to people

| Step | Who |
|---|---|
| Investigate the case, draft the report as `fleetcontrol.goaml-draft/v1` JSON | agents (e.g. `sar-drafter`), as a fleet output in the case's zone |
| Put the draft before people as evidence, decide whether to file | a Decision Room: Admins and Approvers decide, each with a rationale |
| Build the goAML XML from the draft, check it against the FIU's XSD | Fleet Control (`goaml.py`), on request of an Admin or Approver, once the room is decided |
| Upload the XML in goAML Web | a person (the MLRO) |
| Record the FIU's reference | the same person, in Fleet Control; audited |

The reporting person in the XML is the person who prepares the report (their name, their account's email). No goAML
credential passes through Fleet Control, and the only way to mark a report filed is to type the FIU's reference.

## The FIU's schema decides

Every FIU publishes its own goAML XSD. The element names and their order are UNODC's standard (the tables in
`goaml.py`), and a published FIU schema (Sri Lanka FIU, v4-based) has exactly those sequences: a report built from a
realistic draft passes it structurally, and every remaining error is a code-list value, which is the FIU's own. So:

- An Admin loads EMLCU's XSD in **Settings → goAML** (download it from goAML Web; it is not published openly).
  Loading refuses anything that does not compile as XSD 1.1 (goAML schemas carry `xs:assert` business rules) or has no
  `<report>` element. The newest loaded schema is the one reports are checked against; older reports can be checked
  again.
- A report is `ready` only when it passes that schema; `invalid` lists each break as `/report/...path: reason`;
  `unchecked` means no schema is loaded. Only a `ready` report can be recorded as filed.
- The draft uses goAML's element names, so an FIU error names the same field the agent wrote.

Validation is `xmlschema` (XSD 1.1). The test schema in `apps/api/tests/fixtures/goaml-test.xsd` is ours, not an
FIU's: it follows the standard order with short code lists and one assert.

## The draft

```json
{
  "schema": "fleetcontrol.goaml-draft/v1",
  "report_code": "STR",
  "reason": "why the report is made (the FIU's limit applies, 4000 characters in goAML v4)",
  "action": "what the bank did",
  "indicators": ["EMLCU indicator codes"],
  "transactions": [{
    "transactionnumber": "…", "transaction_description": "…", "date_transaction": "2026-08-30T10:15:00",
    "transmode_code": "EMLCU code", "amount_local": 190000,
    "t_from": {"from_funds_code": "…", "from_person": {"first_name": "…", "last_name": "…"}, "from_country": "EG"},
    "t_to_my_client": {"to_funds_code": "…", "to_account": {"institution_name": "…", "swift": "…", "account": "…",
                                                           "currency_code": "EGP", "…": "…"}, "to_country": "EG"}
  }]
}
```

`parties` (`[{"person": {...}, "significance": 5, "reason": "..."}]`) instead of `transactions` makes an activity
report. Lists become repeated elements (`phones` and `addresses` get their goAML wrapper). A client of the bank
(`*_my_client`) needs the KYC fields the FIU's schema requires (in v4: account opening date, balance, status and
signatories or the owning entity; for a person: birthdate, nationality, residence, address, occupation). Those come
from the bank's core banking and KYC systems, so the drafting agent needs read access to them (an MCP server).

## Endpoints

`GET/PUT /api/v1/goaml/settings` (profile: FIU, reporting-entity id, branch, local currency, location) ·
`POST /api/v1/goaml/schema` · `GET /api/v1/goaml/rooms/{room}` (drafts, reports, whether one may be prepared) ·
`POST /api/v1/goaml/reports` · `GET /api/v1/goaml/reports[/{id}[/xml]]` · `POST …/{id}/check` · `POST …/{id}/filed`.
Permissions: `goaml.read` and `goaml.prepare` (Admin, Approver), `goaml.manage` (Admin). A report lives in its room's
content zone: someone who cannot read the zone does not see it.

## Before telling a bank it is EMLCU-ready

Load EMLCU's actual XSD and put a realistic draft through it (the bank's MLRO can supply code lists and a test case),
then upload one report to goAML's test environment if EMLCU offers one. Until then the claim is "prepares and checks
goAML reports against the FIU's schema", which is true of any FIU.
