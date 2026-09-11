# Spike Addendum — Hermes Agent 0.21.2 (source read + partial local run)

Date: 11 September 2026 · Source: `NousResearch/hermes-agent` at commit `bf51fee` (release-dated 2026.9.11), package version **0.21.2**
Method: full source review of the API server, dashboard backend, plugin/hook system, delegation, Langfuse plugin and messaging gateways; the API server's real handlers were exercised in-process (PyPI is blocked in the sandbox, so the servers could not be started over real HTTP). File references are to the repository.

---

## 0. The headline, and the decision it forces

Everything the design assumes is technically reachable, but **not through the surface the research document assumed**. The stable `/v1` API cannot write configuration at all and says so (`/v1/capabilities` returns `admin_config_rw: false`). Every profile, SOUL, skill, toolset, MCP and messaging write lives on the **web dashboard backend** (`hermes_cli/web_routers/*`, port 9119), which is an internal SPA backend with no stability promise and, when exposed off loopback, no non-interactive machine credential. Real-time tool arguments and child-agent tool calls are deliberately dropped from the `/v1/runs` event stream.

**So the answer is the one you proposed mid-spike: install our own client on the Hermes host.** Not as a workaround but as the architecture. Hermes is designed for exactly this: plugins run in-process with hooks that see every tool call and can block it; backend plugins can mount routes; outbound webhooks push signed lifecycle events; the CLI does everything the dashboard does. A small **Fleet Control Agent** installed beside Hermes gives Fleet Control a supported, local, credentialed path to everything, without exposing the dashboard to the network and without depending on an API Nous never promised.

The rest of this document gives the evidence per question and then the resulting architecture.

---

## 1. Findings per spike question

### S1 — Profile create / update / clone / delete over HTTP
**Verdict: dashboard API only; CLI equivalent exists; nothing on `/v1`.**
`hermes_cli/web_routers/profiles.py`: `POST /api/profiles` (create/clone; body `ProfileCreate` with `clone_from`, `clone_all`, `provider`, `model`, `mcp_servers[]`, `keep_skills[]`, `hub_skills[]`), `PATCH /api/profiles/{name}` (rename), `DELETE /api/profiles/{name}`, `POST /api/profiles/import`, `POST /api/profiles/{name}/export`. The `/v1` route table (`gateway/platforms/api_server.py:1515-1563`) has no profile routes. CLI: `hermes profile create|clone|delete|rename|export|import` (`website/docs/reference/profile-commands.md`). On-disk layout per profile: `config.yaml`, `.env` (0600), `SOUL.md`, `skills/`, `profile.yaml` (`hermes_cli/profiles.py:838-890`).
Caveat: profile create fires hub-skill installs as detached subprocesses and returns only PIDs; a caller must poll for completion.

### S2 — Per-profile field writes
**Verdict: dashboard API only, but fully per-profile scoped via `?profile=` or `{name}`.**
SOUL `PUT /api/profiles/{name}/soul` (atomic write) · model `PUT /api/profiles/{name}/model`, `POST /api/model/set?profile=` · skills `PUT /api/skills/toggle?profile=` · toolsets `PUT /api/tools/toolsets/{name}?profile=` · MCP `POST/PUT/DELETE /api/mcp/servers?profile=` · whole config `PUT /api/config?profile=` and `PUT /api/config/raw` · env `PUT /api/env?profile=`. Scoping is implemented by retargeting `HERMES_HOME` per request (`web_routers/_common.py:29`). `/v1/skills` and `/v1/toolsets` are GET-only.

### S3 — Auth, binding, stability
**Verdict: the stable surface refuses writes; the write surface has no machine auth when exposed.**
- Loopback dashboard: one ephemeral token per process (`HERMES_DASHBOARD_SESSION_TOKEN` or random), sent as `X-Hermes-Session` (`web_server.py:308-406`).
- Any non-loopback bind forces the auth gate; `--insecure` is a documented no-op since the June 2026 "hermes-0day" incident ("There is no unauthenticated public-dashboard option", `web_server.py:461-482`). Gated mode accepts OIDC/OAuth session cookies or interactive bearer tokens; the machine-token seam (`dashboard_auth/token_auth.py`) only covers explicitly registered paths, and in-tree that is just `/api/gateway/drain`.
- `COMPAT_MANIFEST.md` governs Python import paths only, states "internal import paths are not a stable API", and self-destructs on **2026-09-14**. It says nothing about HTTP.
- `/v1/capabilities` is the only surface documented as stable "for external UIs, orchestrators and control planes" (`api-server.md:241-260`) and it advertises `admin_config_rw: false`, `jobs_admin: false`, `memory_write_api: false` (`api_server.py:72`).

### S4 — Run events and tool evidence
**Verdict: real-time evidence over `/v1/runs/{id}/events` is names-only; full evidence is available after the fact, and in real time only from inside the process.**
- SSE emits `tool.started {tool, preview}` and `tool.completed {tool, duration, error}` — no arguments, no result (`api_server_runs.py:41-45`). `subagent.start/complete` carry identity and totals (`child_session_id`, `delegation_id`, `parent_id`, tokens, cost) but child tool calls are dropped by design ("high-volume UI noise", `api-server.md:474-490`; code at `api_server_runs.py:160-166`).
- After the fact, `GET /api/sessions/{id}/messages` returns full `tool_calls` with arguments and `role=tool` results, for parent and child sessions alike (`api_server.py:2712-2719, 2926-2956`). Child sessions are hidden from the list unless `?include_children=true`. `GET /v1/runs/{id}` gives `session_id`, which bridges run → transcript.
- In-process hooks `pre_tool_call` / `post_tool_call` receive `args` and `result` in real time (`model_tools.py:660-683`); `pre_tool_call` can **block** a call. Plugin hooks, shell hooks and outbound webhooks all sit on this event bus.
- **Outbound webhooks** (`hooks.outbound:` in config) POST HMAC-signed JSON for any hook event, including `post_tool_call` with `tool_input`, `subagent_stop`, `on_session_end`, with `profile`, `session_id`, `delivery_id` and timestamp (`hooks.md:1848-1935`). Notify-only, best-effort, one retry. This is a config-only way to get real-time tool evidence pushed to Fleet Control.
- Kanban worker runs expose only status/claim/complete events, no tool-level data.

### S5 — Langfuse and OpenTelemetry
**Verdict: Langfuse carries full tool evidence; OTLP carries none; trace lookup is deterministic at session granularity.**
- `plugins/observability/langfuse/` records generations with usage/cost and **tool observations with full arguments and results**, subject to `HERMES_LANGFUSE_CAPTURE ∈ {metadata, sanitized (default), full}`. Subagents appear as spans in the parent trace with metadata only; each child's own calls are a separate root trace keyed by `child_session_id`.
- Trace id is derived: `create_trace_id(seed=f"{session_id}::{task_id}")` where for `/v1/runs` `task_id == session_id or run_id`, so Fleet Control can compute it. It omits `turn_id`, so all turns of one session share one trace. Sampling < 1.0 can mean no trace.
- Enablement is global per instance (`plugins.enabled: [observability/langfuse]` plus `HERMES_LANGFUSE_*` env), not per profile in config, though env can be profile-isolated.
- The OTLP exporter (`agent/monitoring/otlp_exporter.py`) exports gateway health and cron events only; content is explicitly out of scope. Do not plan tool evidence on OTLP.

### S6 — Messaging gateways
**Verdict: Slack, Teams, email and webhooks exist and are configurable over the dashboard API; there is no outbound "send" endpoint, but a supported LLM-free delivery path exists.**
- ~30 platforms; Slack, Teams and Email are plugins with env-key schemas (`plugins/platforms/{slack,teams,email}/plugin.yaml`). `GET/PUT /api/messaging/platforms/{id}` and `/test` write `.env` and `platforms.<id>.enabled` per profile (`web_routers/messaging.py:771-906`), secrets redacted on read.
- No `POST /api/messaging/send`. The intended notify path is an inbound webhook route with `deliver_only: true` (`gateway/platforms/webhook.py:453-473`, documented as "zero LLM tokens, sub-second delivery"): Fleet Control POSTs an HMAC-signed payload to `:8644/webhooks/<route>` and Hermes delivers the rendered template to Slack/Teams/email. Routes are creatable over HTTP (`POST /api/webhooks`) or CLI (`hermes webhook subscribe --deliver slack --deliver-only`). Alternatives: a cron job with `deliver:` and `no_agent: true`; or an agent run using the `send_message` tool (costs tokens, non-deterministic).
- Links: Slack (mrkdwn, unfurl controls) and Teams (`textFormat: markdown`) render links; **email is plain text only** and always sends as a `Re:` reply — acceptable for v1, not pretty.

### S7 — Reading live config for drift
**Verdict: everything needed is readable per profile from the dashboard backend; `/v1` is too thin, and `/v1/skills` is broken.**
- `GET /api/config/raw?profile=` (verbatim `config.yaml`), `GET /api/profiles` (name, path, model, provider, skill count, description), `GET /api/profiles/{name}/soul`, `GET /api/skills?profile=` (with `enabled`), `GET /api/tools/toolsets?profile=`, `GET /api/mcp/servers?profile=`, `GET /api/cron/jobs?profile=all`, `GET /api/env` (redacted).
- **Bug found:** `GET /v1/skills` always returns 500 in this tree: `api_server.py:2643` passes `include_editorial=True` to `_find_all_skills`, whose signature (`tools/skills_tool.py:187`) does not accept it; the test masks it with a permissive mock. Worth an upstream PR — cheap goodwill and a first contact with Nous.
- Gaps: no endpoint returns the assembled system prompt; `/v1/toolsets` reports only the API-server platform's enablement; credentials are never returned in clear.

### S8 — Versioning and stability
**Verdict: three version schemes, no HTTP changelog, and "v2.4" does not exist.**
- Real version: SemVer **0.21.2** (`hermes_cli/__init__.py`), released under CalVer tag `v2026.9.11`, with config schema version 42. The string "v2.4" appears nowhere in the repository; the research document's "Hermes v2.4" and the screens' "Hermes 2.4" are wrong and must be corrected.
- `GET /v1/capabilities` returns **no version field**; use `GET /health` (`version`) or the unauthenticated `GET /api/status` (`version`, `release_date`, `config_version`).
- No CHANGELOG and no recorded breaking change to `/api/*` or `/v1`; release notes exist only as GitHub release bodies. The dashboard API is described everywhere as the SPA's backend, never as a public contract. Assume it changes without notice.

---

## 2. The architecture this forces: the Fleet Control Agent

Install a small, signed component on every Hermes host. It has three parts, each mapping onto a Hermes extension point that is documented and supported.

### 2.1 Hermes plugin `fleetcontrol` (in-process)
- Registers plugin hooks: `pre_tool_call` (policy enforcement — can **block**, giving "Policy blocked" real teeth), `post_tool_call` (tool name, args, result → evidence), `subagent_start/stop`, `on_session_end`, `pre_approval_request`, `post_approval_response`.
- Streams those events to the local daemon (Unix socket) which forwards them to Fleet Control. This replaces the lossy `/v1/runs` stream for managed profiles and gives real-time child-agent evidence, which no HTTP surface provides.
- Optionally mounts a dashboard backend router at `/api/plugins/fleetcontrol/*` for typed, versioned read/write of the fields Fleet Control manages, wrapping `hermes_cli.profiles`, `load_config`/`save_config` and SOUL I/O. This is our contract on top of Hermes internals, so upstream churn is absorbed in one place we control.

### 2.2 Host daemon `fleetctl-agent` (systemd/launchd service)
- Holds an **outbound** mTLS WebSocket to Fleet Control. Nothing on the host is exposed inbound; the dashboard stays on loopback; no dashboard auth problem.
- Executes managed operations: profile create/clone/delete via the `hermes` CLI, field writes via the loopback dashboard API using a session token the daemon sets itself (`HERMES_DASHBOARD_SESSION_TOKEN`) or via the plugin router, config snapshots and restores (tar of the profile dirs), drift scans (reads the same routes as S7, plus file hashes of `SOUL.md` and `config.yaml`), test runs via `/v1/runs`, and gateway lifecycle (`hermes gateway start|stop|restart`).
- Reports the capability matrix on connect: Hermes version from `/health`, dashboard reachability, which plugins are enabled (Langfuse), which gateways are configured.

### 2.3 Zero-install mode ("connect existing", degraded)
For a host where the customer will not install anything yet: Fleet Control uses `/v1` (discovery, runs, sessions) plus, if the operator adds two config entries, `hooks.outbound` pushing `post_tool_call`/`subagent_stop`/`on_session_end` to Fleet Control and the Langfuse plugin for after-the-fact evidence. Reads work; **writes are unavailable**; assurance is "Not verifiable" for anything the outbound hook did not cover. The Instances screen already has the right shape for this: the "What Fleet Control can do here" panel becomes *Agent installed* vs *API only*.

### 2.4 What this changes in the design and the plan
- Instances / connect drawer: two paths — "Install the Fleet Control Agent" (one command, prints a pairing code) and "Connect via API only (read-only)". Show agent version and last heartbeat per instance.
- Plan screen: change rows show method as *Agent* / *API (read-only)* instead of *Dashboard API* / *CLI*. Internally the agent still chooses CLI vs loopback dashboard vs plugin router, but that is no longer a user-facing concept.
- Assurance: evidence source per verdict is *Agent hook* (real time, full), *Session transcript* (after the fact, full), *Langfuse trace* (after the fact, full or sanitized), *Outbound webhook* (real time, inputs only), or none → "Not verifiable".
- Policy: "Policy blocked" is enforced by the plugin's `pre_tool_call` for managed profiles, not merely detected afterwards. This is a stronger product claim than the original design made, and it is true.
- Messaging: delivery rules are implemented as `deliver_only` webhook routes created by the agent; Fleet Control posts signed payloads to the instance's webhook port. Email links stay plain text in v1.
- Correct every "v2.4" to real Hermes versions (0.21.x) in the docs and the screens.
- Slice 1 grows by the agent (plugin + daemon + installer + pairing) and shrinks by the adapter tiers it no longer needs. Net effect roughly +2 weeks; risk goes down substantially because the write path no longer depends on an unstable HTTP surface or on exposing the dashboard.

### 2.5 Risks specific to the agent approach
- Plugins are subject to `HERMES_SAFE_MODE=1` (skips registration) and to import-path churn; the compat manifest shows Nous decomposed the codebase in September and is deleting the shims on 2026-09-14. Pin the plugin to Hermes version ranges and run the nightly compatibility job against the newest release.
- Customers will ask what the plugin can see. Answer honestly: everything the agent sees, including tool arguments; the `sanitized` capture mode should be the default and the plugin should reuse Hermes's own redaction helpers.
- Installing a daemon needs host access; provisioning becomes part of Slice 1 rather than a later vertical, which is consistent with the original research's "deploy or connect" ambition.

---

## 3. Corrections to earlier documents
1. Research document §1.1: `/v1` is stable but read-and-run only; it does not carry the write surface an "independent control plane" needs. §2.2: after-the-fact evidence is better than stated (full transcripts), real-time evidence is worse than stated (no args, no child calls).
2. All references to "Hermes v2.4" / "v2.3.8 → v2.4.0 upgrade" are fictional; Hermes is at 0.21.2.
3. Build document §5.1 (three tiers) is superseded by §2 above; §5.2 spike questions are answered here; §9 Slice 1 scope should add the agent and remove the tier machinery.

## 4. Remaining unknowns (need a real host with PyPI access, about half a day)
- Confirm over real HTTP the exact request/response bodies for the six dashboard routes the agent will use (the in-process harness in `spike-probes.md` exercised `/v1` only).
- Measure plugin-hook overhead on a busy profile, and whether `pre_tool_call` blocking interacts badly with Hermes's own approval flow.
- Verify Teams delivery through `deliver_only` routes (Teams is not in the built-in deliver list and relies on plugin registration order).
- Send the `/v1/skills` fix upstream and see how Nous responds; that response is itself a data point about working with them.
