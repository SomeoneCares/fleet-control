# Fleet Control for Hermes Agent — Build Document v1

Date: 11 September 2026
Status: Working draft for the first engineering team
Inputs reconciled: *Hermes Fleet Studio — Deep Research Validation*, *Hermes Fleet Control Platform — Components & Detailed Feature Plan*, the 18 Stitch screens, and the 23-screen consolidated redesign canvas ("Fleet Control Redesign").

---

## 1. Decisions already taken

These were settled in discussion and are not reopened here.

1. **Positioning.** Fleet Control is an independent control plane *around* Hermes Agent. Hermes remains the runtime; Fleet Control never re-implements a Hermes primitive because it can make it prettier. It designs, blueprints, plans, applies, tests, verifies and governs fleets, and gives business users a governed place to consume outcomes and record decisions.
2. **Name.** Product name is a neutral placeholder, *Fleet Control*, with the descriptor *for Hermes Agent*. "Hermes" is Nous Research's name and is not used as the product brand.
3. **Two experiences, one app.** Administrators and architects use the full portal. Internal business users (compliance officers, approvers) sign in with the same identity and get a stripped *Workspace* experience. There is no separate portal and no multi-tenant "client" product in v1.
4. **Decision Rooms live in the Workspace**, not in the admin portal.
5. **Messaging is delivery, not decision.** Hermes messaging gateways push decision requests and outputs into Teams, Slack, email and webhooks, always with a link back into the Workspace. Replies in chat are never treated as decisions; a decision is recorded only in the Workspace so the audit trail stays complete.
6. **Assurance uses four verdicts only**: *Evidence found*, *No evidence*, *Not verifiable*, *Policy blocked*. No cryptographic-proof, deterministic-replay or "zero hallucination" claims anywhere in the product or its marketing.
7. **Build first, sell second**, but validate in parallel (section 12).

---

## 2. Product definition

### 2.1 One-sentence definition

Fleet Control connects to one or many Hermes Agent instances, turns a business mission into a reviewable, versioned Fleet Blueprint, applies it through a plan, tests and verifies what the agents actually did, detects drift, and gives business users a governed workspace to consume outputs and record decisions.

### 2.2 Audiences and roles

| Role | Who | Experience | Can |
|---|---|---|---|
| Admin | Platform engineering | Admin portal | Everything, including instances, access, messaging |
| Fleet Architect | Product/AI team lead | Admin portal | Design fleets, edit blueprints, create plans, apply to non-production, run tests |
| Operator | Platform/AI ops | Admin portal | Monitor assurance and drift, re-check instances, run workflows, approve staging applies |
| Approver | Compliance officer, MLRO, business lead | Workspace | Decide in Decision Rooms, sign human gates, ask the fleet, read outputs shared with their role |
| Viewer | Auditor, executive | Workspace (read-only) | Read rooms, outputs, audit log |

Five roles, not nine. Sub-permissions are added only when a customer asks.

### 2.3 What Fleet Control owns vs what Hermes owns

Hermes owns: the agent loop, profiles/Bots, skills/toolsets/MCP execution, model providers, sessions and memory, delegation, Kanban, cron, messaging gateways, tool execution, core approvals, its APIs and events.

Fleet Control owns: estate inventory and capability discovery, Fleet Blueprints and desired state, AI-assisted fleet design, plan/diff/apply, drift detection, tests and gates, execution assurance, governance and effective permissions, Decision Rooms and the Workspace, delivery rules for messaging, audit, and the Langfuse/OTel integration (never a replacement for them).

---

## 3. Information architecture

### 3.1 Admin portal navigation (one shell on every screen)

- **Design**: Fleet Architect · Fleet Designer · Agent Studio · Workflows
- **Operate**: Decision Rooms (admin view: list only) · Assurance · Test Lab
- **Estate**: Instances · Integrations · Messaging
- **Govern**: Access · Audit log
- **Library**: Blueprints · Content
- Settings (General, Observability, Approvals, API tokens, Notifications)

Top bar: environment switcher, fleet switcher, search, notifications, user.

### 3.2 Workspace navigation

Home · My decisions · Decision Rooms · Fleet outputs · Ask the fleet · Settings. No environment or fleet switchers; the user's fleet scope comes from their role assignment.

### 3.3 Screen inventory (maps to the redesign canvas)

| # | Screen | Canvas artboard | Release |
|---|---|---|---|
| 1 | Sign in | SignIn | Slice 1 |
| 2 | Instances (empty + connect drawer) | InstancesEmpty | Slice 1 |
| 3 | Instances (populated) | Main | Slice 1 |
| 4 | Drift resolution | Drift | Slice 1 |
| 5 | Fleet Designer (topology, read-mostly) | FleetDesigner | Slice 1 |
| 6 | Agent Studio | AgentStudio | Slice 1 |
| 7 | Plan before apply | ApplyPlan | Slice 1 |
| 8 | Blueprints library | Blueprints | Slice 1 |
| 9 | Audit log | AuditLog | Slice 1 |
| 10 | Access | Access | Slice 1 (roles), Slice 4 (effective-permission inspector) |
| 11 | Settings | Settings | Slice 1 (General, tokens), Slice 3 (Observability) |
| 12 | Fleet Architect (first run) | FleetArchitectEmpty | Slice 2 |
| 13 | Fleet Architect (proposal) | FleetArchitect | Slice 2 |
| 14 | Test Lab | TestLab | Slice 3 |
| 15 | Assurance | Assurance | Slice 3 |
| 16 | Integrations | Integrations | Slice 3 |
| 17 | Workspace home | WorkspaceHome | Slice 4 |
| 18 | Decision Room | DecisionRoom | Slice 4 |
| 19 | Ask the fleet | AskFleet | Slice 4 |
| 20 | Fleet outputs | FleetOutputs | Slice 4 |
| 21 | Messaging (admin) | Messaging | Slice 4 |
| 22 | Content | Content | Slice 4 (minimal: zones, upload, read access) |
| 23 | Workflows | Workflows | Slice 5 |

Explicitly deferred: marketplace, arbitrary workflow engine, custom observability backend, multi-tenant SaaS, Kubernetes operator, managed hosting, provisioning/upgrade waves, media transcription, semantic search over content, formal approval workflows with quorum, SAML/directory sync.

---

## 4. Domain model

### 4.1 Core objects

| Object | Purpose | Notes |
|---|---|---|
| **Instance** | A registered Hermes installation | endpoint, environment, credentials reference, discovered version, capability fingerprint, health |
| **Blueprint** | Desired state of a fleet, versioned | the central artifact; YAML on disk, Git-friendly |
| **BlueprintVersion** | Immutable snapshot | draft → published; applied versions are recorded per instance |
| **Agent** (in blueprint) | A Hermes profile as intended | identity, SOUL sections, model policy, skills, toolsets, MCPs, boundaries, tests |
| **Workflow** (in blueprint) | Ordered steps with human gates | mapped onto Hermes Kanban where available |
| **Policy** (in blueprint) | Fleet-level rule | e.g. cloud models receive redacted input only; external submission requires approval |
| **Test** | Scenario + required/forbidden tools + expected artifact + limits + evaluator | attached to an agent or workflow |
| **Plan** | Computed diff between a BlueprintVersion and an Instance's live state | list of changes, each with method tier and risk |
| **Apply** | Execution record of a plan | snapshot before, per-change result, rollback pointer |
| **Snapshot** | Live config captured before an apply | used for rollback |
| **DriftEvent** | Managed field differs from blueprint | resolution: accept / revert / ignore once / exception |
| **Run** | A Hermes run observed by Fleet Control | linked to agent, instance, trace id |
| **Claim** | A statement extracted from a run's output | subject of assurance |
| **AssertionResult** | Verdict for a claim against a rule | Evidence found / No evidence / Not verifiable / Policy blocked, with evidence pointers |
| **DecisionRoom** | Governed question with evidence, findings, options and a recorded decision | belongs to a case; opened by an orchestrator or a person |
| **Decision** | A person's recorded choice with rationale | append-only; second approver optional |
| **Output** | Artifact the fleet shares with a role | classification, provenance (run, agent, sources) |
| **Channel / DeliveryRule** | Messaging gateway config and event → channel mapping | rules are part of the blueprint |
| **AuditEvent** | Every configuration change, plan, apply, approval, decision | append-only |

### 4.2 Fleet Blueprint schema v1 (illustrative)

```yaml
apiVersion: fleetcontrol/v1
kind: Blueprint
metadata:
  name: aml-investigation
  version: 3
  owner: dana.whitfield
requires:
  hermes: ">=2.4"
  capabilities: [runs, sessions, skills, profiles.write]
mission: >
  Investigate suspicious wire transfers flagged by the SAS Viya anomaly model;
  verify sanctions exposure; trace beneficial ownership; prepare a SAR draft
  for two compliance officers to sign.
policies:
  - id: data-residency
    rule: cloud-model-agents receive redacted summaries only
  - id: external-submission
    rule: any external action requires human approval
agents:
  - id: sanctions-screener
    role: Screens counterparties against OFAC, EU and UN lists
    model: { provider: local, name: llama-4-70b-q4 }
    soul:
      objective: ...
      principles: [...]
      boundaries: [...]
      output_contract: { required: [match_count, matches, list_versions, screened_at] }
    skills: [name-matching, list-versioning]
    mcps: [opensanctions]
    tests: [sanctions-evidence, alias-match, high-confidence-escalates]
workflows:
  - id: case-to-sar-draft
    steps:
      - { agent: case-orchestrator, artifact: case-brief }
      - parallel:
          - { agent: sanctions-screener, artifact: screening-result.json }
          - { agent: ownership-tracer, artifact: ownership-graph.json }
      - { agent: challenger, artifact: challenge-memo }
      - { human_gate: compliance-officer, timeout: 24h, escalate_to: mlro }
      - { agent: sar-drafter, artifact: sar-draft.docx }
      - { open_decision_room: true }
delivery:
  - { when: decision_room.opened, to: teams:aml-investigations, template: decision-request }
targets:
  - { instance: hermes-staging-eu-01, environment: staging }
  - { instance: hermes-prod-eu-01, environment: production, requires_approvals: 2 }
```

Secrets are references, never values. SOUL text is stored in the blueprint with a content hash so drift on SOUL is detectable.

---

## 5. Hermes adapter and the write path

### 5.1 Three tiers (every feature declares which tier it needs)

| Tier | Surface | Stability | Used for |
|---|---|---|---|
| A | Stable API (`/v1/capabilities`, `/v1/runs`, `/v1/skills`, `/v1/toolsets`, sessions, jobs) | Documented as stable | Discovery, running tests, sandbox, run events, assurance inputs |
| B | Dashboard backend API (`/api/config`, `/api/mcp/servers`, skills toggle, cron) | "You can call these directly" — not a stability promise | Writing profiles, config, skills, MCP registrations |
| C | CLI over SSH / local exec (`hermes profile …`) and config files | Unsupported fallback | Profile create/clone where B is missing; explicitly flagged in the plan |

Every Plan change row shows its tier ("via Dashboard API"). The capability matrix per instance (shown on the Instances screen) is computed from Tier A discovery plus a reachability probe of Tier B.

### 5.2 Spike questions (answer before Slice 1 starts; two days)

| # | Question | Pass criterion | Decides |
|---|---|---|---|
| S1 | Can a profile be created, updated and deleted through Tier B? | Round-trip create → read → update → delete on a lab instance | Whether Agent Studio is a UI over an API or a CLI wrapper |
| S2 | Can SOUL, skills and MCP assignment be written per profile through Tier B? | Same, per field | Which blueprint fields are "managed" in v1 |
| S3 | Are Tier B endpoints authenticated and reachable when the dashboard is bound to a non-loopback address? | Bearer token flow works | Connect wizard design |
| S4 | Do run events (`/v1/runs/{id}/events`) include tool-call start/end with tool name and arguments for the parent run and for child/delegated runs? | Tool calls visible with names; child correlation ids present | Whether tool-evidence assertions work without Langfuse |
| S5 | With the bundled Langfuse plugin enabled, can Fleet Control read the trace for a run id and enumerate tool calls and artifacts? | Trace fetched by run id via Langfuse API | Assurance data path |
| S6 | Can a messaging gateway (Teams or Slack) be configured per instance via Tier B and a message sent programmatically with a deep link? | Message arrives with link | Messaging admin scope |
| S7 | Is there a stable way to list profiles and their live config for drift comparison? | Full read of managed fields | Drift detection |
| S8 | What happens across a Hermes minor upgrade to B-tier endpoints? | Diff endpoints between two recent versions | Size of the upstream-tracking budget |

Outcome of the spike is a one-page addendum to this document, and the capability matrix in code.

### 5.3 Upstream tracking

Budget roughly half an engineer permanently for Hermes compatibility: watching releases, re-running the spike checks in CI against the newest Hermes, and updating the adapter. Open an upstream conversation with Nous Research about a stable profile-management API; Fleet Control's viability improves materially if it exists.

---

## 6. Assurance pipeline

1. **Observe**: subscribe to run events for managed profiles (Tier A); optionally fetch the Langfuse trace (S5).
2. **Extract claims**: rule-based first (regex/structured output contracts); an LLM extractor only for free-text outputs, and only in v2.
3. **Check** against rules attached to agents and workflows: required tool called; forbidden tool not called; expected artifact exists (checked in the artifact store the workflow declares); output matches contract; policy constraints (approval present before external action; redacted-input-only for cloud models).
4. **Verdict**: Evidence found / No evidence / Not verifiable (inputs missing, e.g. no trace on that instance) / Policy blocked.
5. **Route**: No evidence and Policy blocked raise operator alerts via delivery rules; verdicts appear on the run, in Test Lab results, on outputs in the Workspace and in Decision Rooms next to each finding.

What this is not: it does not prove correctness of content, and the product copy must never imply it does.

---

## 7. Governance model

- **Effective access = person ∩ agent ∩ downstream system.** The Access screen's inspector evaluates all three and shows which one denied.
- Agents carry their own permissions in the blueprint (content zones, MCPs, external actions). People carry roles. Downstream systems (SAS Viya, core banking) keep their own auth; Fleet Control never bypasses it and only records what the agent was allowed to call.
- Production applies require the number of approvals set on the target (default 2). Staging and lab require none.
- Every plan, apply, approval, decision, access change, drift event and delivery is an audit event. Export is CSV in v1.

---

## 8. Messaging

- Channels are Hermes gateways discovered or configured per instance (Teams, Slack, email/SMTP, Telegram, HTTP webhook).
- Delivery rules map fleet events (Decision Room opened, second approval needed, output shared, assurance No evidence, drift detected) to channels with a message template and a deep link (room, outputs, assurance, instances).
- Delivery rules are part of the blueprint so they promote with it.
- Replies in chat are logged but never recorded as decisions or approvals.

---

## 9. Release plan

Each slice is shippable and demoable on its own. Sizes assume a team of three to four (one product/design, two to three engineers).

### Slice 0 — Spike (2 days)
Section 5.2. Output: addendum + capability matrix.

### Slice 1 — The thin vertical slice (6–8 weeks)
Sign in (email/password; SSO stub) · Connect an instance with capability discovery and the honest "what Fleet Control can do here" panel · Import live profiles into a Blueprint v1 (YAML, Git-friendly) · Fleet Designer as a read-mostly topology with inspector · Agent Studio for managed fields (identity, SOUL, model, skills, MCPs) · Plan (diff) with tiers and pre-flight · Apply to lab/staging with snapshot and rollback · Drift detection and the four resolutions · Blueprints library and version history · Audit log · Roles (five) and basic RBAC.

Exit criterion: a stranger can connect a Hermes instance, change an agent, see the plan, apply it, break it by hand and watch drift appear, then revert. Nothing else counts as done.

### Slice 2 — Fleet Architect (3–4 weeks)
Mission intake and constraints · structured proposal contract (schema v1, versioned independently of Hermes) submitted to a designated architect profile via Tier A runs · proposal rendered as agents/tests/policies with accept/edit/remove · save as blueprint draft · open questions loop.

### Slice 3 — Test Lab and Assurance (5–6 weeks)
Test cases and suites attached to agents and workflows · runs against lab/staging via Tier A · gating: plan pre-flight requires the suite · assurance rules, verdicts and the evidence rail · Langfuse/OTel settings and per-instance enablement · Integrations screen with per-tool allow-lists for MCPs.

### Slice 4 — Workspace and Messaging (5–6 weeks)
Workspace shell and home · Decision Rooms (evidence, findings with verdicts, options, rationale, second approver) · Ask the fleet (orchestrator chat bounded by the person's content access, sources shown) · Fleet outputs · Content (zones, upload, classification, read access for people and agents) · Messaging admin (channels, delivery rules, test message) · effective-permission inspector.

### Slice 5 — Workflows (3–4 weeks)
Ordered steps with parallel groups and human gates, mapped onto Hermes Kanban where available; run and inspect; human gate timeouts and escalation.

Total to a complete v1: roughly six months with a small team, assuming the spike does not surface a blocker.

---

## 10. Stack recommendation

Chosen for proximity to Hermes (Python) and for a small team that must move fast without a platform build-out.

- **Backend**: Python 3.12, FastAPI, SQLAlchemy, Pydantic models for the blueprint schema (also used to generate JSON Schema for validation and export). The Hermes adapter is a Python package with the three tiers behind one interface; the CLI tier shells out or runs over SSH (asyncssh).
- **Workers**: a lightweight task queue (arq or Celery with Redis) for drift polling, test runs, assurance checks and message delivery.
- **Database**: PostgreSQL. Blueprints stored as YAML text plus a parsed JSONB column; versions immutable. Audit as an append-only table.
- **Frontend**: React with TypeScript, Vite, Tailwind configured from the client's DESIGN.md tokens, a small in-house component set matching the redesign shell (no heavy UI framework). Topology in Fleet Designer rendered with React Flow in read-mostly mode.
- **Auth**: OIDC via Authlib; local accounts for v1; SSO in Slice 4 or on first enterprise request.
- **Observability integration**: Langfuse Python client for trace reads; OpenTelemetry exporter for Fleet Control's own metrics.
- **Packaging**: single Docker Compose stack (api, worker, postgres, redis, web) for v1; Helm chart only when a customer needs it.
- **Repo**: monorepo (`apps/api`, `apps/web`, `packages/blueprint-schema`, `packages/hermes-adapter`), with a CI job that runs the spike checks against the latest Hermes release nightly.

If the team is stronger in TypeScript than Python, the same architecture works with Node (Fastify) and a thin Python sidecar for the CLI tier; the adapter interface is the part to keep language-agnostic.

---

## 11. Risks and open questions

| Risk | Mitigation |
|---|---|
| No stable write API in Hermes | Tier B/C with explicit labelling; upstream conversation; keep managed-field set small |
| Hermes ships desired-state/drift natively | Structural position: multi-instance, governance, evidence and Workspace are what a runtime vendor is least likely to build; keep the adapter thin so feature overlap is cheap to absorb |
| Assurance data incomplete on some instances | "Not verifiable" verdict is first-class; per-instance Langfuse enablement from Settings |
| Upstream velocity | Nightly compatibility CI; half-engineer budget; capability matrix per instance |
| Two audiences dilute focus | Slice order forces the control plane to work before the Workspace exists |
| Trademark on "Hermes" | Neutral brand; descriptor use only; check with counsel before launch |

Open: which Hermes profile acts as the architect (a Fleet Control-provided profile shipped as a blueprint asset, or the customer's own); how SAR-style documents are produced (Hermes tool vs Fleet Control template); whether decision records need e-signature in the first regulated customer.

---

## 12. Validation in parallel

Six to eight conversations with people who run more than a handful of agents in regulated or cost-sensitive settings. Use the Workspace and Plan screens as props. Ask the seven jobs from the research document (stand up ten agents repeatably; change them safely; prove what runs where; test before rollout; know when an agent falsely claims completion; enforce fleet-wide policy; promote from lab to production) and listen for incident stories, not feature wishes. Two outcomes to watch for: whether operators react more to Plan/Drift or to Assurance, and whether business stakeholders recognise the Decision Room as something they would use instead of email and meetings.

---

## 13. Immediate next actions

1. Run the spike (section 5.2) on a lab Hermes instance; write the addendum.
2. Confirm the stack (section 10) or the TypeScript variant.
3. Set up the monorepo, CI with the nightly Hermes compatibility job, and the design tokens package from DESIGN.md.
4. Start Slice 1 with the Instances screen and the adapter's Tier A discovery; the first demo is "connect and see what this instance can do".
