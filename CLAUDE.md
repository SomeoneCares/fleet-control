# CLAUDE.md — project context for Claude Code sessions

Read this first, then `docs/build-document.md` and `docs/spike-addendum-hermes-0.21.2.md`.

## What this is
Fleet Control for Hermes Agent: an independent control plane around Hermes Agent (NousResearch/hermes-agent, currently 0.21.x).
It designs fleets as versioned Blueprints, plans and applies them to Hermes instances through an installed Fleet Control Agent,
verifies what agents actually did, detects drift, and gives business users a governed Workspace (Decision Rooms) in the same app.
Hermes stays the runtime; we never re-implement its primitives. Product name is a neutral placeholder; never brand as "Hermes …".

## Decisions already made (do not reopen without asking Basem)
- Writes to Hermes go through the Fleet Control Agent on the host (plugin + daemon), never through an exposed dashboard API.
- Two experiences, one app: admin portal and business-user Workspace. Decision Rooms live in the Workspace.
- Messaging delivers; it never records decisions. Assurance verdicts are exactly: Evidence found / No evidence / Not verifiable / Policy blocked.
- Stack: Python 3.11+, FastAPI, Pydantic v2, SQLAlchemy Core on PostgreSQL (SQLite for dev and tests), React+TS+Vite+Tailwind for `apps/web`.
- Hermes version strings are 0.21.x. "v2.4" was fiction in early docs; if you see it, fix it.

## Layout
- `packages/blueprint_schema` — the data model. `Blueprint.managed_fields()` defines what the agent reconciles.
- `packages/hermes_plugin/fleetcontrol` — in-process Hermes plugin (hooks + policy.json enforcement). Hook signatures match hermes-agent docs `website/docs/user-guide/features/hooks.md`.
- `apps/agent/fleetctl_agent` — host daemon. `hermes_local.py` pins the dashboard/API routes we depend on.
- `apps/api/fleetcontrol_api` — control plane. `planner.compute_plan` is the source of truth for plan rows and agent jobs.
- `scripts/test.sh` runs all suites with the stdlib runner; `scripts/hermes_compat_check.py <hermes-agent checkout>` must stay green.
- `design/` — 23 screen sources; `python3 build.py <Name> <nav>` regenerates an artboard. Tokens in `design/DESIGN.md`.

## Current state (2026-09-16)
- CI green on GitHub (tests + API end-to-end smoke). Nightly Hermes compat job configured.
- DONE: real-host capture on Hermes 0.21.2 → `docs/dashboard-capture-0.21.2*.json`, and `hermes_local.py` reconciled
  against it (session header is `X-Hermes-Session-Token`; `/api/profiles` and `/api/mcp/servers` return wrapped lists;
  profile create answers 200 with `model_set: false` when Hermes rejects the model). Re-capture on each new Hermes minor:
  `scp scripts/run_capture.sh hermes@<host>:~/ && ssh hermes@<host> bash run_capture.sh` (it starts its own temporary
  loopback dashboard and leaves an existing one alone); save as `docs/dashboard-capture-<version>*.json`.
- DONE: `apps/web` Slice 1 screens — Instances (+Connect drawer, empty state), Blueprints, ApplyPlan, Drift — with
  Tailwind tokens generated from `design/` (`apps/web/scripts/tokens.mjs`). `scripts/dev_seed.py` fills a local API
  with demo data and simulated agents; see `apps/web/README.md` to run it. Drift resolution API: accept/revert/ignore/exception.
- DONE: Fleet Designer (topology from the workflow), Agent Studio (edits land on drafts; applied versions are immutable,
  also on re-upload), Audit log (filters, CSV export at `/api/v1/audit/export`).
- DONE: sign-in with local accounts (stdlib scrypt, HttpOnly session cookie, `X-Fleet-Control: 1` required on writes,
  lockout after 5 failures) and the five roles enforced on every `/api/v1` route (`auth.PERMISSIONS`). Production
  approvals: Admin or Approver only, never the plan's creator, one per person; production applies by Admin or Operator.
  Access screen (roles, people, agent permissions) and a Workspace home for Approvers/Viewers. First admin comes from
  `FLEETCONTROL_ADMIN_EMAIL` (no default password). Local dev: `scripts/dev_api.py` then `scripts/dev_seed.py`;
  passwords land in git-ignored `.fleetcontrol-dev-credentials.json`. Claude must not type passwords into the web form:
  the user signs in for browser checks.
- DONE (2026-09-16): Fleet Control Agent installed on the lab host as instance `hermesbo-lab-01` (systemd user units
  `fleetctl-dashboard`, its own dashboard on 127.0.0.1:9129 with a token only the daemon knows, and `fleetctl-agent`;
  source copied to `~/fleet-control-src`). The installer no longer writes to `~/.hermes/.env` and leaves the LAN
  dashboard alone; the plugin is copied but not enabled, and the capability report says so. The daemon reaches a
  local dev API through an SSH reverse tunnel: `ssh -N -R 127.0.0.1:18080:127.0.0.1:8080 hermes@<host>`.
  Claude can open that tunnel and SSH to the host directly (an earlier note here claiming the sandbox blocks it
  was wrong); the Viya host is `viya@69.30.204.121`.
- DONE (2026-09-16): Slice 1 exit test passed on `hermesbo-lab-01` (Hermes 0.21.2) with a one-agent blueprint
  (`fc-exit-test`, throwaway, deleted afterwards; the business profiles stayed unmanaged): connect → plan (Dana,
  Fleet Architect) → apply (Sam, Operator) → hand edit of SOUL.md + `hermes tools enable web` → drift on both
  fields → revert → clean scan. What the real host taught us: a new profile starts with 18 toolsets and ~53 skills
  on, so creates now `sync_skills`/`sync_toolsets` to exactly the blueprint's lists; `hermes-agent` is an
  essential skill Hermes never disables (`ESSENTIAL_SKILLS`), so the agent leaves it unmanaged and the compat check
  pins the set; Operators cannot create plans from a blueprint, only revert plans.
- DONE (2026-09-16): blueprint from imported live profiles (`importer.py`, `POST /api/v1/instances/{id}/
  blueprint-from-live`, "Create blueprint from what runs here" on Instances; Admin/Fleet Architect). SOULs are kept
  verbatim (`Soul.raw` keeps its whitespace); a profile that would not plan to zero changes is left out with the
  reason. On `hermesbo-lab-01` all five profiles import exactly and the plan against the instance is empty.
- DONE (2026-09-16): database store (`store.py`, SQLAlchemy Core): one table per collection, key columns plus a
  JSON document (JSONB on PostgreSQL); blueprints as YAML + parsed JSON; insert-only audit; pairing/agent tokens
  stored as SHA-256; jobs in the database (queued work survives a restart; claims and plan transitions are
  conditional updates, so two API processes cannot double-deliver or double-apply). All writes go through store
  methods; never mutate a returned record. `FLEETCONTROL_DATABASE_URL` picks the database (unset = in-memory SQLite,
  what the tests use); `scripts/dev_api.py` uses `.fleetcontrol-dev.db` (`--fresh` starts over); CI runs the API
  tests a second time against a PostgreSQL 16 service. Unit tests must pass `Store("sqlite://")` explicitly.
- DONE (2026-09-16): Settings — General (workspace name, sign-in length, longest API token), Approvals (minimum
  approvals per environment; `planner.compute_plan(default_approvals=...)` takes max(floor, blueprint target)),
  API tokens (`Authorization: Bearer fct_…`, hashed, expiring, revocable; a token acts as its owner and cannot mint
  tokens or change the password; writes by token need no X-Fleet-Control header). Observability and Notifications
  tabs arrive with Slices 3 and 4. `settings.py` holds defaults and bounds.
- DONE (2026-09-16): Slice 2 Fleet Architect (`architect.py`, `/api/v1/architect/*`, screen at `/architect`). Mission +
  constraints go to a designated architect profile as a `hermes_run` agent job (`/p/<profile>/v1/runs`, polled; the
  daemon runs it on a background thread); the answer must follow `fleetcontrol.proposal/v1` (validated; dangling
  references dropped and listed under `adjustments`; unusable answers kept with the reason and the raw text).
  Accept/edit/remove per agent, open questions → Ask again (the previous proposal, decisions and answers go back),
  save accepted agents as a Blueprint v1 draft (constraints become policies). The architect is any profile you
  choose, or the `fleet-control-architect` blueprint (one tool-less `fc-architect` profile, planned and applied like
  any blueprint); on `hermesbo-lab-01` it is installed and chosen. Multiplex gateways serve new profiles without a
  restart. Hermes 0.21.2 checks a named profile's OWN `API_SERVER_KEY` (its `.env`) for `/p/<profile>/` requests and
  never falls back to the default key; the agent creates one (mode 600, stays on the host) the first time Fleet
  Control asks a profile that has none, and the gateway picks it up without a restart. Admin/Fleet Architect ask and decide; every role that reads blueprints can read sessions.
- DONE (2026-09-16): Slice 3 part 1 — Test Lab and Assurance (`testlab.py`, `/api/v1/testlab/*`, `/api/v1/assurance/*`,
  screens at `/testlab` and `/assurance`). A test runs its scenario on the target agent's profile on a lab or staging
  instance (`run_test` job, background thread); the agent returns output, usage, duration and every tool call from the
  Hermes session transcript (`GET /api/sessions/{id}/messages`, paged oldest-first). `evaluate()` judges required and
  forbidden tools (MCP tools are `mcp_<server>__<tool>`), the expected artifact, the evaluator and the limits, and
  turns them into claims with the four verdicts; missing transcript = Not verifiable, never a guess. Tests are edited
  on drafts (`PUT/DELETE /api/v1/blueprints/{name}/{version}/tests/{id}`). Production applies are gated on the
  version's agent tests having passed (Settings → Approvals, `require_tests_for_production`, default on; workflow
  tests do not gate until Slice 5). Assurance: claims, 24 h summary, CSV export.
- DONE (2026-09-16): Slice 3 part 2 — Integrations (`integrations.py`, `/api/v1/integrations*`, screen at
  `/integrations`). What exists comes from the instances (agent jobs `mcp_discover`, which connects to each MCP
  server to list its tools, and `mcp_write` for add/remove/enable/disable); who may use it comes from the blueprints
  (an agent's `mcps`/`model` and the tool allow- and deny-lists in policies). Server configuration lives on the
  instance, never in a blueprint, and **no credentials pass through Fleet Control**: a server is added by url or
  command only. `hermes_compat_check.py` now pins `/api/mcp/servers/{name}/test` and `/enabled`.
- DONE (2026-09-16): Settings → Observability (read-only, honest): per instance, whether Assurance can read the
  Hermes session transcript (any paired agent), and the state of the `fleetcontrol` and Langfuse plugins from the
  capability report. Enabling a plugin changes the instance, so it is done there; Langfuse keys never reach Fleet
  Control. Slice 3 is complete (Test Lab, Assurance, Integrations, Observability).
- DONE (2026-09-16): Slice 4 part 1 — Content zones (`content.py`, `/api/v1/content/*`, screen at `/content`) and
  Fleet outputs (`outputs.py`, `/api/v1/outputs*`, screen at `/outputs`). A zone names the roles that may read it
  (an Admin always may) and carries a classification; every file and output lives in one, so the zone is the single
  answer to "who sees this". An output keeps its provenance — which agent produced it, from which run, on which
  instance — and agents file their own through `POST /agent/v1/instances/{id}/outputs`.
- DONE (2026-09-16): Slice 4 part 2 — Decision Rooms backend (`rooms.py`, `/api/v1/rooms*`). A room holds one
  question, a case, its content zone, evidence (files, outputs, assurance claims with their verdicts, notes),
  findings from agents, and the options. Decisions are append-only, one per person, each with a rationale; the room
  closes when the decisions it needs are in, and a named second approver keeps it open until they decide too (the
  room reports when the two chose differently instead of hiding it). Admins, Fleet Architects and Operators open
  rooms; **Admins and Approvers decide** (`rooms.decide`); nobody opens a room into a zone they could not then read.
  Agents open rooms and file findings through `/agent/v1` and never decide. Each decision is a read-change-write in
  one transaction (`store.update_room`), so two people deciding at once cannot lose one another's decision.
  Screens: `/rooms` (the list), `/rooms/:id` (evidence rail · what the fleet found · decision rail, per
  `design/screens/DecisionRoom`) and `/my-decisions` (waiting for me · decided by me · open elsewhere). Verdicts
  reuse `VERDICT_TONE` from `lib/testlab.ts`, so a verdict looks the same in Assurance and in a room. The second
  approver is a free-text email validated by the API, not a dropdown: `users.read` is Admin-only and Operators
  open rooms. The Workspace home now shows the decisions waiting for you and the fleet's recent outputs.
- NOTE: on Basem's Windows box `python3` is the Microsoft Store stub; run the suites as
  `PYTHON=.venv/Scripts/python bash scripts/test.sh` (with `PYTHONIOENCODING=utf-8`).
- DONE (2026-09-20): SAS Viya MCP Server connected to `hermesbo-lab-01` and discovered by Fleet Control
  (`docs/sas-viya-mcp-integration.md` — read it before touching this). SAS's own MCP server runs in-cluster on
  the Viya host (`viya.internal/sas-mcp/mcp`, read-only, 51 of 92 tools); it is registered **per Hermes profile**
  by url with `auth: oauth`, and Integrations lists all 51 tools with `Used by: None` until a blueprint declares
  `mcps: [sas-viya]`. Four traps, all documented: the ingress sends only its leaf certificate (the root CA comes
  from `viya/sas-viya-ca-certificate-secret`) and **Hermes ignores the system trust store** — the CA must go in
  the venv's `certifi/cacert.pem` and `SSL_CERT_FILE` must point at that same file; device flow is not
  advertised, so `--flow browser` (Dynamic Client Registration works, no SASLogon client needed); the OAuth
  callback listens on the host, so forward the port and keep the login's stdin open or it kills its own
  listener; profiles do not inherit root `mcp_servers`, so registration and tokens are per profile (N agents =
  N consents, or `allowRawBearer` + a service account, trading per-agent attribution for one identity).
- DONE (2026-09-24): the five defects from the SAS week. Test runs can be stopped (`tests.run`) and deleted
  (`tests.manage`, Admin); a cancelled run never counts. Integrations health is per profile (`degraded` when some
  fail), `used_by` reads the newest *applied* version (drafts show as `planned_by`), discoveries merge per profile.
  A blank MCP probe error gets the agent's own diagnosis (`diagnose_endpoint`). Apply reconciles `mcps`:
  `copy_mcp` copies a registration from another profile on the same host (never env values, header tokens or OAuth
  tokens; OAuth logins become the plan's `manual_steps`), `remove_mcp` removes. Room evidence and findings carry a
  `basis` (source / analytical / interpretation / assumption / judgment; only people judge); an analytical finding
  names its tool and Fleet Control checks it against the run or the plugin's session events (`rooms.check_tool`).
- DONE (2026-09-24): Slice 4 Ask the fleet (`ask.py`, `/api/v1/ask/*`, screen at `/ask`, `ask.use` = admin portal
  roles + Approver). Fleet Control picks the sources (files, outputs, rooms) from zones the person may read AND the
  orchestrator profile is granted by an applied blueprint's `content_zones`, numbers them S1…, and sends only those as
  a `hermes_run` with `transcript: true`. Citations are checked (invented ones dropped); the answer's verdict is
  Evidence found only if it cites sources and the transcript shows no tool calls. Conversations are the owner's alone;
  saving to Fleet outputs is allowed only into zones whose readers could read every cited source, with the strictest
  classification. The orchestrator is a settings choice (`ask`), or the `fleet-control-orchestrator` blueprint
  (one tool-less `fc-orchestrator` granted chosen zones), planned and applied like any blueprint.
- DONE (2026-09-24): Slice 4 Messaging (`messaging.py`, `/api/v1/messaging*`, screen at `/messaging`; read =
  admin portal roles, manage = Admin). A channel is a messaging platform an instance's Hermes gateway has connected
  (`<platform>:<id>` is what blueprint `delivery` rules name). The agent keeps one `deliver_only` webhook route per
  channel (`fc-<id>`, prompt `{text}`) whose secret stays in `~/.fleetctl-agent/route-secrets.json`, and posts each
  message to it on loopback, signed `X-Webhook-Signature-V2` (hex HMAC of `<ts>.<body>`) with `X-Request-ID` =
  the delivery id. So no inbound port and no secret in Fleet Control (the build document's "Fleet Control posts"
  became "the agent posts": same route, no exposed port). Rules come from each blueprint's newest applied version and
  are edited on drafts (`PUT /api/v1/blueprints/{name}/{version}/delivery`). Events: room opened, second approval
  needed, output shared, assurance No evidence / Policy blocked, drift detected, apply completed/failed; one message
  per event per channel. Messages carry the event, case id and a link (`portal_url` in Settings → General); a room's
  question or an output's name only on channels with `show_titles`. Enabling the webhook platform restarts the
  gateway (Hermes does it), so it is an explicit Admin action with a warning.
- PLAN (Basem, 2026-09-16): complete the whole portal before Docker, every screen working end to end (real backend,
  real Hermes runs on the lab host where needed), in build-document order: Slice 2 Fleet Architect → Slice 3 Test
  Lab, Assurance, Integrations (+ Settings → Observability) → Slice 4 Workspace, Decision Rooms, Ask the fleet,
  Fleet outputs, Messaging, Content (+ Notifications, Access inspector) → Slice 5 Workflows.
- LATER: Docker Compose packaging; OIDC sign-in mapped onto the same roles; Alembic migrations before the first
  schema change on a real install.

## Working agreements
- Keep tests runnable with `python3 -m unittest` (no pytest-only features). Add tests with every module.
- Never put secrets in the repo or in blueprints (`secret://` refs only). Mask tokens in captured JSON.
- Commit messages: imperative summary + short body. Basem pushes; if you have push access, push to `main` after `scripts/test.sh` passes.
- When touching anything that talks to Hermes, re-run `scripts/hermes_compat_check.py` against a fresh clone of upstream.
