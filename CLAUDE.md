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
- Stack: Python 3.11+, FastAPI, Pydantic v2, PostgreSQL later (in-memory now), React+TS+Vite+Tailwind for `apps/web`.
- Hermes version strings are 0.21.x. "v2.4" was fiction in early docs; if you see it, fix it.

## Layout
- `packages/blueprint_schema` — the data model. `Blueprint.managed_fields()` defines what the agent reconciles.
- `packages/hermes_plugin/fleetcontrol` — in-process Hermes plugin (hooks + policy.json enforcement). Hook signatures match hermes-agent docs `website/docs/user-guide/features/hooks.md`.
- `apps/agent/fleetctl_agent` — host daemon. `hermes_local.py` pins the dashboard/API routes we depend on.
- `apps/api/fleetcontrol_api` — control plane. `planner.compute_plan` is the source of truth for plan rows and agent jobs.
- `scripts/test.sh` runs all suites with the stdlib runner; `scripts/hermes_compat_check.py <hermes-agent checkout>` must stay green.
- `design/` — 23 screen sources; `python3 build.py <Name> <nav>` regenerates an artboard. Tokens in `design/DESIGN.md`.

## Current state (2026-09-14)
- CI green on GitHub (tests + API end-to-end smoke). Nightly Hermes compat job configured.
- DONE: real-host capture on Hermes 0.21.2 → `docs/dashboard-capture-0.21.2*.json`, and `hermes_local.py` reconciled
  against it (session header is `X-Hermes-Session-Token`; `/api/profiles` and `/api/mcp/servers` return wrapped lists;
  profile create answers 200 with `model_set: false` when Hermes rejects the model). Re-capture on each new Hermes minor:
  `scp scripts/run_capture.sh hermes@<host>:~/ && ssh hermes@<host> bash run_capture.sh` (it starts its own temporary
  loopback dashboard and leaves an existing one alone); save as `docs/dashboard-capture-<version>*.json`.
- DONE: `apps/web` Slice 1 screens — Instances (+Connect drawer, empty state), Blueprints, ApplyPlan, Drift — with
  Tailwind tokens generated from `design/` (`apps/web/scripts/tokens.mjs`). `scripts/dev_seed.py` fills a local API
  with demo data and simulated agents; see `apps/web/README.md` to run it. Drift resolution API: accept/revert/ignore/exception.
- NEXT: the remaining Slice 1 screens — Fleet Designer (read-mostly topology), Agent Studio, Audit log, Access (roles).
- THEN: PostgreSQL store, sign-in (local then OIDC), Docker Compose packaging, Slice 1 exit test on a lab instance.

## Working agreements
- Keep tests runnable with `python3 -m unittest` (no pytest-only features). Add tests with every module.
- Never put secrets in the repo or in blueprints (`secret://` refs only). Mask tokens in captured JSON.
- Commit messages: imperative summary + short body. Basem pushes; if you have push access, push to `main` after `scripts/test.sh` passes.
- When touching anything that talks to Hermes, re-run `scripts/hermes_compat_check.py` against a fresh clone of upstream.
