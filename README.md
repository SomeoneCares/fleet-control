# Fleet Studio for Hermes Agent

Independent control plane around [Hermes Agent](https://github.com/NousResearch/hermes-agent): design fleets as
versioned blueprints, plan and apply them through approvals, test them, verify what agents actually did, detect drift,
and give business users a governed Workspace where people decide. Hermes stays the runtime.

Built for regulated work first: the reference use is AML investigation, from case intake to a suspicious transaction
report prepared for the FIU in **goAML** (Egypt: EMLCU; see [docs/goaml.md](docs/goaml.md)).

## Status (October 2026)

All five slices of the [build document](docs/build-document.md) work end to end, against a real Hermes host where it
matters (the lab instance `hermesbo-lab-01`, Hermes 0.21.5):

| Slice | What works |
|---|---|
| 1 | Sign-in and five roles, instances and the Fleet Studio Agent, blueprints, plan → approve → apply, drift and its four resolutions, Fleet Designer, Agent Studio, audit log, blueprints from what an instance runs |
| 2 | Fleet Architect: a mission to an architect profile, a validated proposal, accepted agents saved as a draft |
| 3 | Test Lab (agent and workflow tests), Assurance (four verdicts from the session transcript), Integrations (MCP servers, their tools, who may use them, login expiry), Settings → Observability |
| 4 | Workspace, Decision Rooms, Ask the fleet, content zones, fleet outputs, Messaging, personal notifications, the Access inspector |
| 5 | Workflows: steps, parallel groups, human gates with escalation, a closing Decision Room; run as Hermes runs or on Hermes Kanban |
| + | goAML reports from decided rooms, checked against the FIU's own XSD |

Not done yet: container packaging (a Helm chart for Kubernetes estates, then Docker Compose), OIDC single sign-on,
Alembic migrations before the first schema change on a real install, and reading Langfuse traces into Assurance.

## Layout

```
docs/
  build-document.md         the product plan (with an implementation-status section)
  goaml.md                  goAML reports for the FIU (Egypt: EMLCU)
  sas-viya-mcp-integration.md   SAS Viya's MCP server on a Hermes host: TLS, OAuth logins, their 24-hour limit
  spike-addendum-hermes-0.21.2.md, spike-probes-raw.md   why the agent architecture (historical)
  dashboard-capture-<version>*.json   the Hermes dashboard routes the agent uses, captured on a real host
  lab/                      blueprints used on the lab host
  research/                 market and component research
design/     23-screen redesign: screens/*.content.html sources, build.py, DESIGN.md tokens
packages/
  blueprint_schema/   fleetcontrol_blueprint: Fleet Blueprint v1 (Pydantic), JSON Schema export, example blueprints
  hermes_plugin/      fleetcontrol: the in-process Hermes plugin (evidence hooks + policy enforcement)
apps/
  agent/              fleetctl-agent: host daemon (hermes_local.py pins every Hermes route and file it depends on)
  api/                fleetcontrol-api: FastAPI control plane, one module per area (below)
  web/                React client for every screen; see apps/web/README.md
scripts/    test.sh · hermes_compat_check.py · install-agent.sh · dev_api.py · dev_seed.py · run_capture.sh
```

`apps/api/fleetcontrol_api/`: `planner` (plans and agent jobs) · `drift` · `importer` · `edits` · `architect` ·
`testlab` · `integrations` · `content` · `outputs` · `rooms` · `ask` · `messaging` · `notifications` · `access` ·
`workflows` · `goaml` · `auth` (roles and permissions) · `settings` · `store` (SQLAlchemy Core) · `main` (routes).

## How the pieces fit

1. **Blueprint** (`packages/blueprint_schema`) is the desired state: agents, policies, workflows, tests, delivery
   rules. `Blueprint.managed_fields()` is what the agent reconciles per profile (description, model, SOUL, skills,
   toolsets, MCP servers).
2. **Fleet Studio Agent** = plugin + daemon on the Hermes host. The daemon pairs once, then long-polls Fleet Studio
   for jobs (import, apply, drift scan, policy push, test and workflow runs, MCP discovery, Kanban, message delivery)
   and carries them out through the `hermes` CLI and a loopback dashboard only it can use. It reports its capabilities,
   Kanban state and MCP login expiry with every heartbeat. Nothing inbound is exposed; no credential leaves the host.
   The plugin captures tool calls as evidence and enforces each profile's `policy.json` (block / approve).
3. **API** turns blueprint vs live state into a plan (`planner.compute_plan`), gates it on approvals and tests, and
   queues the agent jobs. Assurance judges every run from its Hermes session transcript. People decide in Decision
   Rooms and workflow gates; agents never decide.

## Run the tests

```
sh scripts/test.sh            # stdlib runner: blueprint schema, plugin, agent, API, scripts
cd apps/web && npm test       # Vitest
python3 scripts/hermes_compat_check.py /path/to/hermes-agent   # must stay green on every Hermes release
```
On Windows (Git Bash), with a venv: `python -m venv .venv && .venv/Scripts/pip install -e packages/blueprint_schema -e packages/hermes_plugin -e apps/agent -e apps/api[dev]`,
then `PYTHON=.venv/Scripts/python PYTHONIOENCODING=utf-8 sh scripts/test.sh` (`python3` is the Microsoft Store stub there).
The API tests also run against PostgreSQL 16 in CI.

## Run it locally

```
.venv/Scripts/python scripts/dev_api.py        # the API on :8080 with a bootstrap admin (python3 on Linux/macOS)
.venv/Scripts/python scripts/dev_seed.py       # optional: demo data, one person per role, simulated agents
cd apps/web && npm install && npm run dev      # http://localhost:5173
```
Passwords land in the git-ignored `.fleetcontrol-dev-credentials.json`. Data lives in the database
`FLEETCONTROL_DATABASE_URL` names: PostgreSQL in deployments (`postgresql+psycopg://…`; install `apps/api[postgres]`),
`.fleetcontrol-dev.db` for `dev_api.py` (`--fresh` starts over), in-memory SQLite when unset (tests). Tables are
created on start. On a first real start set `FLEETCONTROL_ADMIN_EMAIL` (there is no default password) and
`FLEETCONTROL_COOKIE_SECURE=1` behind HTTPS. Every `/api/v1` route needs a signed-in person (writes also send
`X-Fleet-Control: 1`) or an API token (`Authorization: Bearer fct_…`, acting as its owner).

To connect a Hermes host: Instances → Connect → run the install command `scripts/install-agent.sh` on the host → the
instance reports in → import what runs there, or plan a blueprint onto it.

Design canvas: the "Fleet Studio Redesign" artifact on claude.ai. Regenerate a screen with `cd design && python3 build.py <Name> <nav>`.
