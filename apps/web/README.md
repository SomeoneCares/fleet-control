# apps/web

React + TypeScript (Vite, Tailwind v4) client for Fleet Control. Slice 1 admin screens: **Instances** (with the
Connect drawer and the day-one empty state), **Blueprints** (library, version history, YAML import, plan creation),
**Plan before apply** (approvals, apply, outcome), **Drift** resolution (accept / revert / ignore once / exception),
**Fleet Designer** (read-mostly topology laid out from the workflow, with an inspector), **Agent Studio** (edit an
agent's managed fields; applied versions are immutable, so saving creates a draft) and the **Audit log** (filters,
CSV export). The other navigation items open a page naming the slice they arrive in.

## Run it

From the repo root, three terminals (the API keeps everything in memory, so restarting it clears the data):

```
.venv/Scripts/python -m uvicorn fleetcontrol_api.main:app --port 8080    # the API (python3 -m ... on Linux/macOS)
.venv/Scripts/python scripts/dev_seed.py                                  # optional: demo data + simulated agents
cd apps/web && npm install && npm run dev                                 # http://localhost:5173, proxies /api to :8080
```

`dev_seed.py` creates three instances (two with a simulated Fleet Control Agent that answers import, drift-scan,
policy and apply jobs), applies the example AML blueprint to staging, edits two fields there by hand so there is
drift to resolve, and leaves a production plan waiting for approvals.

## Design tokens

Colours, the type ramp, radii and control sizes come from `design/DESIGN.md` and `design/SHELL.md`.
`scripts/tokens.mjs` turns them into `src/styles/tokens.css` (a Tailwind v4 `@theme`); `npm run dev` regenerates it
and `npm run build` fails if the committed file is stale. Do not hand-edit it or copy colours into components.

## Checks

`npm test` (Vitest: view logic and the token generator) · `npm run typecheck` · `npm run build` (tokens check,
typecheck, production bundle). CI runs all three in the `web` job.

## Layout

- `src/api/client.ts` — typed client for `/api/v1`; shapes mirror `apps/api/fleetcontrol_api/main.py`
- `src/lib/view.ts` — pure view logic (instance status, capability rows, plan phase, drift rows, labels), unit-tested
- `src/lib/topology.ts` — Fleet Designer layout (workflow rows, or delegation layers); `src/lib/soul.ts` mirrors `Soul.render()`
- `src/components/` — the shell (navigation from build document §3.1) and the small UI kit
- `src/screens/` — Instances, ConnectDrawer, DriftModal, Blueprints, ApplyPlan, Designer, Studio, Audit, NotYet

Not yet: sign-in (the API trusts a placeholder user, so one person cannot give both production approvals),
discovery for API-only instances, restoring a snapshot from the UI, sandbox runs in Agent Studio (Test Lab, Slice 3),
and the Access screen, which arrives with sign-in and the five roles.
