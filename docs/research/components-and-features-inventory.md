# Hermes Fleet Control Platform — Components & Detailed Feature Plan
## 1. Purpose
This document consolidates the agreed product direction into a platform component model and detailed feature inventory. The platform is an independent visual control plane around Hermes Agent. Hermes remains the underlying agent runtime; the platform adds deployment lifecycle, fleet architecture, graphical management, collaboration, content, testing, assurance, operations, governance and enterprise integration.
## 2. Product principles
- **Hermes remains the runtime:** The platform does not fork or replace Hermes agent execution. It prefers supported Hermes APIs and adapts as native capabilities evolve.
- **Control plane, not competing agent framework:** The product designs, configures, validates, governs and operates Hermes fleets.
- **Desired state before live state:** Blueprints represent intended configuration; deployments are realizations of those blueprints.
- **AI proposes; people control:** Hermes can recommend fleet architecture and changes, but deployment and sensitive actions follow explicit governance.
- **Security is contextual:** Human, agent and downstream-system permissions combine to determine effective access.
- **Evidence over status:** A green run is not enough; assurance should verify important claims against actual evidence.
- **Business-first interaction:** Normal users should primarily use orchestrator chat, fleet content and Decision Rooms rather than low-level Hermes controls.
- **Open integration model:** SAS Viya MCP is the flagship enterprise intelligence integration, while the architecture remains open to other MCPs and enterprise systems.

## 3. Platform component map
1. **Hermes Instance & Estate Manager** — Deploy, connect, register, inspect, maintain and govern one or many Hermes installations.
2. **Fleet Architect** — Use Hermes AI capabilities to translate a business mission into a proposed specialist fleet before anything is deployed.
3. **Fleet Blueprint Manager** — Maintain the desired-state definition of a fleet independently from what is currently deployed.
4. **Visual Fleet Designer** — Graphically design and understand the fleet as an architecture, organization and capability map.
5. **Agent / Profile Studio** — Configure, version, test and govern individual Hermes profiles without manually editing underlying files for routine operations.
6. **Workflow Studio** — Visually design and supervise multi-agent business processes while preferring Hermes-native execution capabilities whenever available.
7. **Fleet Collaboration & Content Workspace** — Provide the day-to-day workspace where people interact with fleets, agents and governed business content.
8. **Test & Validation Lab** — Validate profiles, skills, integrations and workflows before deployment or promotion.
9. **Execution Assurance Engine** — Verify that an agent's claims and outputs are supported by execution evidence rather than merely trusting a successful status or fluent answer.
10. **Fleet Operations & Observability** — Operate fleets as a whole rather than inspecting isolated sessions one at a time.
11. **Governance, Identity, RBAC & Change Management** — Control who can see, change, execute or approve platform, fleet, content and agent actions.
12. **Asset Library & Marketplace** — Reuse approved fleet designs, agents, workflows, tests, policies and integration packs across teams and eventually across customers.
13. **Decision Rooms** — governed multi-agent workspaces for high-value human decisions.
14. **Enterprise Intelligence Integrations** — governed access to enterprise analytics and decisioning, with SAS Viya MCP as the flagship reference integration.

## 4. Delivery-stage legend
- **MVP**: required to prove the end-to-end product thesis.
- **P2**: second release/expansion after core validation.
- **P3**: advanced differentiation and scaling.
- **Enterprise**: controls typically needed for regulated or large organizations.
- **Future**: ecosystem or scale features intentionally deferred.

## 1. Hermes Instance & Estate Manager

Deploy, connect, register, inspect, maintain and govern one or many Hermes installations.

- **[MVP]** Connect an existing Hermes instance using a supported endpoint and credentials.
- **[MVP]** Validate connectivity, authentication and basic health before registration.
- **[MVP]** Discover Hermes version and query machine-readable capabilities at connection time.
- **[MVP]** Inventory profiles/agents, configured providers/models, skills, toolsets, MCP servers and available runtime surfaces.
- **[MVP]** Maintain an estate inventory with instance name, environment, owner, version, status, last check and fleet assignments.
- **[MVP]** Health dashboard for gateway/API availability, model/provider reachability, MCP availability and core runtime services.
- **[MVP]** Register multiple Hermes instances and map fleets to one or more target instances.
- **[MVP]** Secure credential storage using references rather than exposing secrets in the UI.
- **[P2]** Guided deployment wizard for new Hermes installations.
- **[P2]** Remote Linux deployment with prerequisite checks, package installation, baseline configuration and health verification.
- **[P2]** Container/Docker deployment option.
- **[P2]** Backup Hermes configuration before lifecycle changes.
- **[P2]** Upgrade-readiness check: version compatibility, API capability changes, configuration risk and test requirements.
- **[P2]** Controlled upgrade workflow with pre-check, backup, upgrade, post-check and rollback decision.
- **[P2]** Maintenance mode and controlled restart from the portal.
- **[P3]** Fleet-wide version compliance and upgrade-wave management.
- **[P3]** Cloud VM deployment templates and infrastructure hooks.
- **[Future]** Kubernetes and large-scale distributed estate deployment patterns.
- **[Enterprise]** Environment segmentation: development, test, staging and production.
- **[Enterprise]** Instance ownership, business unit mapping, criticality and support metadata.

## 2. Fleet Architect

Use Hermes AI capabilities to translate a business mission into a proposed specialist fleet before anything is deployed.

- **[MVP]** Natural-language mission intake describing objective, users, expected outputs, constraints and operating context.
- **[MVP]** Ask the designated Hermes planning/orchestrator profile to recommend whether a fleet is required and what structure fits the mission.
- **[MVP]** Recommend fleet size, specialist members, roles and responsibilities.
- **[MVP]** Recommend orchestrator role and delegation/collaboration model.
- **[MVP]** Recommend required skills, tools, toolsets and MCP integrations.
- **[MVP]** Recommend initial workflows and human approval points.
- **[MVP]** Recommend content-library requirements and source types.
- **[MVP]** Present recommendations as a proposed blueprint, never deploy automatically.
- **[MVP]** User can accept, reject, edit, add, remove or merge recommendations before creation.
- **[P2]** Recommend model/provider choices per fleet member using availability, cost, capability and latency constraints.
- **[P2]** Capacity planning inputs: expected work volume, concurrency, SLA, geography and operating hours.
- **[P2]** Estimate fleet operating cost before deployment.
- **[P2]** Generate alternative fleet designs such as lean, balanced and high-assurance options.
- **[P2]** Gap analysis against currently connected Hermes capabilities.
- **[P2]** Recommend missing skills/MCPs/content sources required to make the proposed fleet viable.
- **[P3]** Use historical execution data to recommend right-sizing, consolidation or specialization of agents.
- **[P3]** Detect role overlap and propose simplification.
- **[Enterprise]** Apply organization design policies such as mandatory reviewer/challenge roles or separation of duties.

## 3. Fleet Blueprint Manager

Maintain the desired-state definition of a fleet independently from what is currently deployed.

- **[MVP]** Create a versioned Fleet Blueprint as the canonical desired-state object.
- **[MVP]** Blueprint captures mission, members, roles, models, skills, tools, MCPs, permissions, workflows, content access, policies, tests and deployment targets.
- **[MVP]** Separate Blueprint from Deployment so one design can be promoted across environments.
- **[MVP]** Draft, review and publish blueprint versions.
- **[MVP]** Clone a blueprint into a new fleet or variant.
- **[MVP]** Compare blueprint versions with added, removed and modified objects.
- **[MVP]** Generate an Apply Plan showing proposed changes before changing Hermes.
- **[MVP]** Rollback to a previously approved blueprint version.
- **[MVP]** Export/import blueprint in a portable machine-readable format.
- **[P2]** Branch blueprint variants for experiments or business-unit customization.
- **[P2]** Template variables for environment-specific values.
- **[P2]** Blueprint validation rules and dependency checks before publication.
- **[P2]** Compatibility warnings when Hermes capability/version cannot support a blueprint feature.
- **[P2]** Blueprint promotion path: development to test to staging to production.
- **[P3]** Policy-as-code hooks for blueprint validation.
- **[Enterprise]** Formal approval workflow before production publication.
- **[Enterprise]** Signed/immutable approved releases for regulated environments.

## 4. Visual Fleet Designer

Graphically design and understand the fleet as an architecture, organization and capability map.

- **[MVP]** Canvas showing orchestrators, specialist agents, groups and relationships.
- **[MVP]** Drag-and-drop creation, placement, grouping and reorganization of fleet members.
- **[MVP]** Open an agent directly from the canvas into Agent/Profile Studio.
- **[MVP]** Show role, status, model, key skills and deployment location on demand.
- **[MVP]** Visualize delegation and communication relationships.
- **[MVP]** Visualize which workflows use which agents.
- **[MVP]** Filter by role, group, status, environment and instance.
- **[MVP]** Zoom, fit-to-view, minimap and search.
- **[P2]** Capability overlay showing skills, tools and MCP access.
- **[P2]** Security overlay showing permission boundaries and sensitive integrations.
- **[P2]** Runtime overlay showing active/idle/degraded/offline state.
- **[P2]** Cost overlay showing relative consumption by member.
- **[P2]** Content-access overlay showing which library zones each agent may read/write.
- **[P2]** Design validation warnings for orphan agents, missing orchestrator paths, unresolved dependencies or excessive privilege.
- **[P3]** Auto-layout options for organization, workflow and capability views.
- **[P3]** Compare two fleet architectures visually.
- **[Enterprise]** Share read-only architecture views with auditors/executives.

## 5. Agent / Profile Studio

Configure, version, test and govern individual Hermes profiles without manually editing underlying files for routine operations.

- **[MVP]** Agent identity: name, description, business role, responsibilities, owner and group.
- **[MVP]** View/edit SOUL and other supported profile configuration in a structured editor with raw-mode fallback.
- **[MVP]** Assign default model/provider and permitted fallback options.
- **[MVP]** Assign skills and remove skills.
- **[MVP]** Assign toolsets and MCP servers.
- **[MVP]** Configure allowed collaboration/delegation relationships.
- **[MVP]** Display effective capabilities resolved from model, skills, toolsets and MCPs.
- **[MVP]** Version profile configuration and compare changes.
- **[MVP]** Open direct test session from the profile.
- **[MVP]** Show recent runs, failures, token use and assurance results for the profile.
- **[P2]** Structured prompt/SOUL sections for mission, principles, boundaries, escalation and output requirements.
- **[P2]** Model fallback policy and routing rules where Hermes supports them.
- **[P2]** Per-agent budget, concurrency, timeout and runtime constraints.
- **[P2]** Workspace/storage assignment and artifact rules.
- **[P2]** Per-agent content-library permissions.
- **[P2]** Per-agent tool/MCP approval requirements.
- **[P2]** Clone agent and create role variants.
- **[P3]** AI-assisted profile improvement based on test failures and run history.
- **[P3]** Detect overlapping or contradictory instructions across profile configuration.
- **[Enterprise]** Dual-control changes for high-privilege production agents.

## 6. Workflow Studio

Visually design and supervise multi-agent business processes while preferring Hermes-native execution capabilities whenever available.

- **[MVP]** Create workflow from natural-language description using Hermes assistance.
- **[MVP]** Visual canvas for multi-agent steps, dependencies and hand-offs.
- **[MVP]** Agent node, input node, output node and human approval node.
- **[MVP]** Associate steps with fleet agents and expected outputs/artifacts.
- **[MVP]** Run or test a workflow from Studio through supported Hermes surfaces.
- **[MVP]** Version workflow definitions and compare revisions.
- **[MVP]** Visualize workflow execution state against the design.
- **[MVP]** Workflow templates reusable across fleet blueprints.
- **[P2]** Condition/decision nodes where supported by Hermes or a thin Studio coordination layer.
- **[P2]** Parallel branches, fan-out and fan-in.
- **[P2]** Failure path, retry, timeout and escalation design.
- **[P2]** Subworkflow/reference nodes.
- **[P2]** Notification nodes and human decision gates.
- **[P2]** Data/artifact contracts between steps.
- **[P2]** Pin test inputs at a step and rerun a selected segment where feasible.
- **[P3]** Trajectory comparison between workflow versions.
- **[P3]** Reusable workflow components and organization standards.
- **[Enterprise]** Workflow change approvals and production promotion controls.

## 7. Fleet Collaboration & Content Workspace

Provide the day-to-day workspace where people interact with fleets, agents and governed business content.

- **[MVP]** Fleet-level chat with the designated orchestrator as the default human interaction model.
- **[MVP]** Direct chat with specialist agents where RBAC permits it.
- **[MVP]** Conversation history linked to fleet, user, agent and session/run context.
- **[MVP]** Attach authorized content to a conversation.
- **[MVP]** Context-aware chat launched from an agent, run, workflow, artifact or Decision Room.
- **[MVP]** Fleet Content Library with folders, search, tags, metadata and permissions.
- **[MVP]** Supported content types: PDF, Word, Excel, PowerPoint, Markdown, text, CSV, JSON, images, audio, video, logs, configuration files, source code, URLs, generated reports and agent-generated artifacts.
- **[MVP]** Upload, download, preview and version library content where format support permits.
- **[MVP]** Associate content with fleets, agents, workflows, runs and business cases.
- **[MVP]** Content classification such as Public, Internal, Confidential and Restricted.
- **[MVP]** Separate human permissions from agent permissions for library content.
- **[P2]** Audio playback and video playback within the portal.
- **[P2]** Audio/video transcription with searchable transcript and timestamps.
- **[P2]** Speaker labels where transcription provider supports them.
- **[P2]** AI summaries and chapter/segment extraction for long audio/video.
- **[P2]** Independent permissions for original media, transcript and derived summary.
- **[P2]** Content lifecycle metadata: owner, source, effective date, expiry, retention and approval state.
- **[P2]** External repositories/connectors such as document-management, cloud-drive and knowledge systems.
- **[P2]** Content provenance for agent-generated outputs including source documents, run, workflow and approving human.
- **[P3]** Semantic search/RAG across authorized fleet content.
- **[P3]** Policy-aware retrieval that considers both user and agent authorization.
- **[Enterprise]** Legal hold, retention controls and controlled export.

## 8. Test & Validation Lab

Validate profiles, skills, integrations and workflows before deployment or promotion.

- **[MVP]** Run an isolated test chat/session against a selected agent/profile.
- **[MVP]** Provide fixed test prompts, files and expected outcomes.
- **[MVP]** Capture tool calls, arguments, outputs, generated artifacts, duration and token use available from Hermes/integrations.
- **[MVP]** Create reusable test cases and group them into suites.
- **[MVP]** Pass/fail criteria based on response, artifact existence or explicit assertions.
- **[MVP]** Run test suite before applying a production blueprint change.
- **[MVP]** Store historical results by agent/profile/workflow version.
- **[P2]** Regression tests for SOUL, skill, model, MCP or workflow changes.
- **[P2]** Test missing-tool, unavailable-MCP, timeout and bad-input scenarios.
- **[P2]** Mock or pin selected tool results when supported by the integration path.
- **[P2]** Compare responses and tool trajectories across model/profile versions.
- **[P2]** Golden test datasets and expected artifacts.
- **[P2]** Automatic test recommendations generated from past failures.
- **[P3]** Scheduled continuous regression suites.
- **[P3]** A/B evaluation of candidate agent/profile configurations.
- **[Enterprise]** Promotion gates requiring defined test suites to pass.

## 9. Execution Assurance Engine

Verify that an agent's claims and outputs are supported by execution evidence rather than merely trusting a successful status or fluent answer.

- **[MVP]** Define execution assertions attached to agents, workflows or outputs.
- **[MVP]** Artifact-existence assertion: required output was actually created.
- **[MVP]** Tool-evidence assertion: required tool/integration was actually invoked.
- **[MVP]** Source-use assertion: claimed source was actually read or retrieved where trace evidence permits verification.
- **[MVP]** Output-count/value assertion against structured evidence.
- **[MVP]** Mark claims as Verified, Unsupported, Failed or Not Verifiable.
- **[MVP]** Show evidence behind each assurance result.
- **[MVP]** Link failed assurance to run, agent, tool calls and artifacts.
- **[P2]** Cross-agent provenance: verify downstream output is based on upstream approved evidence.
- **[P2]** Detect missing required review/approval steps.
- **[P2]** Policy assertions such as 'no external publication without human approval'.
- **[P2]** Confidence scoring based on available evidence and trace completeness.
- **[P2]** Business-domain assertion packs configurable by customers.
- **[P3]** AI-assisted contradiction detection between agent claims and evidence.
- **[P3]** Assurance trends by fleet/agent/workflow/version.
- **[Enterprise]** Immutable assurance evidence packages for audit-sensitive processes.

## 10. Fleet Operations & Observability

Operate fleets as a whole rather than inspecting isolated sessions one at a time.

- **[MVP]** Fleet health summary: healthy, degraded, offline and unknown members.
- **[MVP]** Active, queued, completed, blocked and failed work counts.
- **[MVP]** Live activity timeline across agents in a fleet.
- **[MVP]** Drill from fleet to agent to run/session to tool call/artifact where supported.
- **[MVP]** Instance, provider/model and MCP availability overview.
- **[MVP]** Run search and filtering by fleet, agent, status, user, date and workflow.
- **[MVP]** Operational alerts for failed/blocked work and unavailable critical integrations.
- **[MVP]** Basic token/usage metrics where exposed by Hermes/providers.
- **[P2]** Cost estimation and allocation by fleet, agent, workflow and business unit.
- **[P2]** Latency, success/failure rate, throughput and utilization trends.
- **[P2]** Agent workload and queue imbalance detection.
- **[P2]** Cross-instance fleet view for distributed deployments.
- **[P2]** Operational chat: ask orchestrator what needs attention and why.
- **[P2]** Service-level objectives and threshold alerts.
- **[P3]** Predictive capacity and spend forecasting.
- **[P3]** Automated recommendations to rebalance or resize fleets.
- **[Enterprise]** Long-term metrics retention and external observability export.

## 11. Governance, Identity, RBAC & Change Management

Control who can see, change, execute or approve platform, fleet, content and agent actions.

- **[MVP]** User authentication and organization/workspace membership.
- **[MVP]** Role-based access control at platform, workspace, instance, fleet, agent, workflow, content and run scope.
- **[MVP]** Built-in roles: Platform Administrator, Organization Administrator, Fleet Architect, Fleet Operator, Agent Developer, Content Manager, Security Administrator, Auditor and Standard User.
- **[MVP]** Granular permissions beneath roles for view, modify, execute, deploy, chat, approve, share and administer actions.
- **[MVP]** Human authorization: what the signed-in person may access or do.
- **[MVP]** Agent authorization: what each agent may access or do.
- **[MVP]** Delegated authorization: effective access when an agent acts on behalf of a user.
- **[MVP]** Effective permission principle: user permission intersected with agent permission and downstream-system authorization.
- **[MVP]** Audit log for login, configuration, deployment, execution, approval, permission and content events.
- **[MVP]** Change history with who/what/when and affected blueprint/profile/workflow.
- **[P2]** Approval workflows for sensitive production changes.
- **[P2]** Separation of duties between designer, approver and operator.
- **[P2]** Configuration drift detection between approved blueprint and actual Hermes state.
- **[P2]** Drift actions: accept, revert, investigate or create exception.
- **[P2]** SSO/OIDC integration.
- **[P2]** MFA and session-security policies.
- **[P2]** Access review reports and stale privilege detection.
- **[P3]** Policy engine for conditional controls based on environment, data classification, action and risk.
- **[P3]** Just-in-time elevated access and time-bound permissions.
- **[Enterprise]** SAML, enterprise directory integration and delegated administration.
- **[Enterprise]** Immutable audit export and compliance evidence packages.

## 12. Asset Library & Marketplace

Reuse approved fleet designs, agents, workflows, tests, policies and integration packs across teams and eventually across customers.

- **[MVP]** Private organization library for Fleet Blueprint templates.
- **[MVP]** Agent/profile templates.
- **[MVP]** Workflow templates.
- **[MVP]** Test-suite and assertion templates.
- **[MVP]** Policy and RBAC templates.
- **[MVP]** Import/export reusable assets with metadata and version.
- **[MVP]** Search, tags, categories and ownership.
- **[P2]** Skill and MCP integration packs with prerequisites and setup instructions.
- **[P2]** Business-role starter kits such as Bid Office, Marketing and Social Media fleets.
- **[P2]** Organization approval/certification mark for trusted assets.
- **[P2]** Dependency and compatibility metadata.
- **[P3]** Community/private marketplace model.
- **[P3]** Ratings, adoption metrics and update notifications.
- **[Future]** Commercial marketplace and publisher ecosystem if business validation supports it.

## 13. Decision Rooms

- **[MVP]** Create a Decision Room as a governed workspace around a defined business decision.
- **[MVP]** Assign a Decision Orchestrator and specialist fleet members.
- **[MVP]** Capture decision question, alternatives, criteria, assumptions, constraints and required evidence.
- **[MVP]** Allow specialist agents to submit structured analyses and recommendations.
- **[MVP]** Display agreement, disagreement and unresolved questions across fleet members.
- **[MVP]** Include a dedicated Challenge/Devil's Advocate role to test assumptions and recommendation robustness.
- **[MVP]** Separate source evidence, analytical evidence, agent interpretation, assumptions and human judgment.
- **[MVP]** Human decision status: pending, approved, rejected, deferred or requires more analysis.
- **[P2]** Scenario comparison and sensitivity questions.
- **[P2]** Re-run selected assumptions and preserve decision-history versions.
- **[P2]** Generate decision pack / executive summary with full provenance.
- **[P2]** Link authorized Content Library material and enterprise analytics results.
- **[P2]** Decision-specific RBAC and confidential-subroom support.
- **[P3]** Decision-quality metrics: evidence completeness, assumption sensitivity and unresolved disagreement.
- **[Enterprise]** Formal committee approval workflow, voting/endorsement and immutable decision record.

## 14. Enterprise Intelligence Integrations

- **[MVP]** Treat enterprise MCPs as governed capability providers, not merely generic connectors.
- **[MVP]** SAS Viya MCP as flagship reference integration.
- **[MVP]** Discover available SAS-exposed tools/capabilities and present them in Studio.
- **[MVP]** Assign approved SAS capabilities to specific fleet members.
- **[MVP]** Respect SAS-native authentication and authorization rather than replacing it.
- **[MVP]** Record which SAS tool/model/decision capability contributed to an agent result when evidence is available.
- **[MVP]** Surface structured SAS outputs into agent workflows and Decision Rooms.
- **[P2]** Organization-level catalog of enterprise analytical capabilities.
- **[P2]** Usage policy per integration: which fleets, agents, users and environments may invoke which capability.
- **[P2]** Health, latency and error monitoring for enterprise MCPs.
- **[P2]** Version/model metadata for governed analytical assets where exposed.
- **[P2]** Test enterprise MCP capability access before production promotion.
- **[P3]** Additional enterprise intelligence integrations following the same governed model.
- **[Enterprise]** Per-capability usage audit, cost allocation and approval rules.

## 15. Platform core services
- **Identity and Session Services:** Authentication, organization/workspace membership, sessions, MFA/SSO hooks and authorization context.
- **Secrets and Credential Services:** Encrypted secret storage/references for Hermes endpoints, model providers, MCPs and external systems.
- **Event and Activity Bus:** Normalized platform events such as instance.connected, agent.started, run.failed, artifact.created, assertion.failed and drift.detected.
- **Configuration and Version Store:** Blueprints, agents, workflows, policies, content metadata and immutable version history.
- **Operational Data Store:** Runs, sessions, health, events, metrics, costs and status summaries.
- **Artifact and Content Storage:** File/object metadata, controlled storage references, versions, classifications and provenance.
- **Search and Discovery:** Cross-platform search respecting RBAC across fleets, agents, runs, content and assets.
- **Notification Service:** In-product, email/webhook and later enterprise messaging notifications for operational and governance events.
- **Integration Gateway:** Hermes APIs, MCP discovery, SAS Viya MCP and future external integrations.
- **Audit Service:** Tamper-resistant append-oriented record of security, governance and significant business actions.
- **Public API / Automation Surface:** Documented API and webhook layer so customers can integrate the portal with enterprise systems.

## 16. RBAC reference roles
- **Platform Administrator:** Platform-wide setup, identity, instances, security and global configuration.
- **Organization Administrator:** Manage users, workspaces, fleets and policies within an organization boundary.
- **Fleet Architect:** Design fleets, blueprints, agents and workflows; generally cannot self-approve sensitive production deployment.
- **Fleet Operator:** Monitor and operate approved fleets, handle runs and interact with orchestrators.
- **Agent Developer:** Develop and test profiles, SOUL, skills and agent behavior without broad production authority.
- **Content Manager:** Manage fleet content, classification, versioning and publication controls.
- **Security Administrator:** Manage access policy, secrets, privileged integrations and approval requirements.
- **Auditor:** Read-only access to approved configuration, runs, evidence, conversations and audit history.
- **Standard User:** Use authorized fleet workspaces, orchestrator chat and approved content without platform administration.

## 17. Business validation reference use cases
- **Banking - AML Investigation Fleet:** Validates governed specialist collaboration, case-scoped content, strict RBAC, SAS analytics/decisioning, human review, evidence provenance and execution assurance.
- **Executive Decision Room - Capital Allocation:** Validates multi-agent analytical collaboration, SAS forecasting/optimization/model use, explicit assumptions, challenge/debate, scenario comparison and traceable human decision-making.

## 18. Recommended MVP proof
The first commercial proof should demonstrate a complete lifecycle rather than a large UI surface: connect a Hermes instance; discover existing capabilities; describe a business mission; let Hermes propose a fleet; review the proposed visual blueprint; configure agent/content/RBAC access; apply to a non-production environment; chat with the orchestrator; run tests; execute a representative workflow or Decision Room; verify evidence with Execution Assurance; monitor the run; detect configuration drift; and show the full audit/change record.

## 19. Explicit non-goals for the initial product
- Replacing Hermes as the agent runtime.
- Forking Hermes or depending on unstable internal Python imports/databases when supported APIs exist.
- Reimplementing enterprise analytics already provided by SAS or similar platforms.
- Making every Hermes capability editable through Studio on day one.
- Building a public marketplace before the core control-plane value is validated.
- Allowing normal business users unrestricted direct access to every specialist agent by default.
