# Fleet Control for Hermes Agent

Independent control plane around [Hermes Agent](https://github.com/NousResearch/hermes-agent): design fleets as versioned blueprints, plan and apply them safely, verify what agents actually did, detect drift, and give business users a governed workspace for outcomes and decisions. Hermes stays the runtime.

## Layout

```
docs/       build-document.md (the plan), spike-addendum-hermes-0.21.2.md (why the agent architecture), research/
design/     23-screen redesign: screens/*.content.html sources, build.py, canvas.json, review/ previews, DESIGN.md tokens
packages/
  blueprint_schema/   fleetcontrol_blueprint — Fleet Blueprint v1 (Pydantic), JSON Schema export, example blueprint
  hermes_plugin/      fleetcontrol — the in-process Hermes plugin (evidence hooks + policy enforcement)
apps/
  agent/              fleetctl-agent — host daemon: plugin socket, outbound connection, jobs (import, plan-apply, drift, policy push, snapshot, tests)
  api/                fleetcontrol-api — FastAPI control plane (web routes + agent transport), planner, in-memory store
  web/                React client: Slice 1 screens (Instances, Blueprints, Plan, Drift, Designer, Studio, Audit, Access, Settings) and the Slice 2 Fleet Architect; see apps/web/README.md
scripts/    test.sh · hermes_compat_check.py (nightly against upstream) · install-agent.sh (one-liner on a Hermes host)
```

## How the pieces fit (Slice 1)

1. **Blueprint** (`packages/blueprint_schema`) is the desired state. `Blueprint.managed_fields()` is the exact set the agent reconciles: description, model, SOUL hash, skills, toolsets, MCPs per profile.
2. **Fleet Control Agent** = plugin + daemon on the Hermes host.
   - The plugin registers `pre_tool_call` / `post_tool_call` / `subagent_*` / session hooks, streams events (args and results, redacted) to the daemon over a local socket, and enforces the per-profile `policy.json` the daemon writes (block / approve).
   - The daemon pairs once with Fleet Control, then long-polls for jobs and executes them against Hermes through the `hermes` CLI and the loopback dashboard API (`X-Hermes-Session-Token` header). `scripts/install-agent.sh` runs a dashboard just for the daemon on `127.0.0.1:9129` with a token only the daemon knows, so a dashboard you already run and `~/.hermes/.env` are left alone. Nothing inbound is exposed.
3. **API** computes a plan from blueprint vs the instance's imported live state (`planner.compute_plan`), gates it on approvals, and turns it into two agent jobs: `push_policy` then `apply` (with a snapshot first, stop on first failure).

## Run the tests

```
sh scripts/test.sh            # stdlib runner; needs only pydantic + pyyaml
python3 scripts/hermes_compat_check.py /path/to/hermes-agent
```
On Windows (Git Bash), with a venv: `python -m venv .venv && .venv/Scripts/pip install -e packages/blueprint_schema -e packages/hermes_plugin -e apps/agent -e apps/api[dev]`, then `PYTHON=.venv/Scripts/python sh scripts/test.sh` — or from PowerShell: `$env:PYTHON=".venv/Scripts/python"; & "C:\Program Files\Git\bin\sh.exe" scripts/test.sh`. The plugin/daemon socket tests use loopback TCP there, as the code does.

## Run the API locally

```
pip install -e packages/blueprint_schema -e apps/api[dev]
uvicorn fleetcontrol_api.main:app --port 8080
```
Every `/api/v1` route needs a signed-in person (session cookie from `POST /api/v1/auth/login`; writes also send
`X-Fleet-Control: 1`), or an API token from Settings → API tokens (`Authorization: Bearer fct_…`; it acts as its
owner). On the first start set `FLEETCONTROL_ADMIN_EMAIL` (and optionally `FLEETCONTROL_ADMIN_PASSWORD`;
otherwise a one-time password is printed), and `FLEETCONTROL_COOKIE_SECURE=1` behind HTTPS. For local development,
`python scripts/dev_api.py` does this for you and keeps the password in the git-ignored `.fleetcontrol-dev-credentials.json`;
`python scripts/dev_seed.py` then adds demo data and one person per role.

Data lives in the database `FLEETCONTROL_DATABASE_URL` names: PostgreSQL in deployments
(`postgresql+psycopg://user:password@host:5432/fleetcontrol`; install `apps/api[postgres]`), SQLite for development
(`scripts/dev_api.py` keeps `.fleetcontrol-dev.db` at the repo root; `--fresh` starts over), and an in-memory SQLite
database when it is unset (tests). Tables are created on start.

Then: `POST /api/v1/instances` → copy `install_command` → run `scripts/install-agent.sh` on the Hermes host → `POST /api/v1/instances/{id}/import` → `POST /api/v1/blueprints` → `POST /api/v1/plans` → `POST /api/v1/plans/{id}/apply`.

## Status

Scaffold. Schema, planner, plugin and daemon job logic are unit-tested; the API is smoke-tested in CI. The dashboard routes the daemon uses are verified against a real Hermes 0.21.2 host (`docs/dashboard-capture-0.21.2*.json`). The Slice 1 exit test passed on that host. Slice 2 (Fleet Architect) works against a real architect profile. Not yet done: Slices 3–5 of the build document, Docker Compose packaging, and OIDC single sign-on.

Design canvas: the "Fleet Control Redesign" artifact on claude.ai. Regenerate a screen with `cd design && python3 build.py <Name> <nav>`.
