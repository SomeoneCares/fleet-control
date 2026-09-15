# apps/web

React + TypeScript (Vite, Tailwind v4) client for Fleet Control. Slice 1 admin screens: **Instances** (with the
Connect drawer, the day-one empty state, and a blueprint created from what an instance runs), **Blueprints** (library, version history, YAML import, plan creation),
**Plan before apply** (approvals, apply, outcome), **Drift** resolution (accept / revert / ignore once / exception),
**Fleet Designer** (read-mostly topology laid out from the workflow, with an inspector), **Agent Studio** (edit an
agent's managed fields; applied versions are immutable, so saving creates a draft) and the **Audit log** (filters,
CSV export). **Sign in** gates everything; **Settings** holds General, Approvals and API tokens; the **Fleet Architect** sends a
mission to an architect Hermes profile and saves the agents you accept as a blueprint draft; **Test Lab** runs a
blueprint's tests on a lab or staging instance and **Assurance** shows each claim with its verdict and evidence; the **Access** screen (Admin) manages people and shows roles and agent
permissions; Approvers and Viewers land on a **Workspace home** listing plans that wait for their approval. Buttons
follow the signed-in role, and a missing Approve button says why. The other navigation items open a page naming the
slice they arrive in.

## Run it

From the repo root, three terminals (the API keeps its data in `.fleetcontrol-dev.db`, so a restart keeps it;
`scripts/dev_api.py --fresh` starts over):

```
.venv/Scripts/python scripts/dev_api.py        # the API with a bootstrap admin (python3 on Linux/macOS)
.venv/Scripts/python scripts/dev_seed.py       # optional: demo data, demo people, simulated agents
cd apps/web && npm install && npm run dev      # http://localhost:5173, proxies /api to :8080
```

Sign in with the admin email and password from `.fleetcontrol-dev-credentials.json` at the repo root (git-ignored;
created by `dev_api.py`). `dev_seed.py` adds one person per role to the same file (Dana is the Fleet Architect, Marcus
and Lena are Approvers, Sam is an Operator, Riya a Viewer), so you can try the approval rules. It also creates three instances (two with a simulated Fleet Control Agent that answers import, drift-scan,
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

Not yet: single sign-on (OIDC), discovery for API-only instances, restoring a snapshot from the UI, the
person ∩ agent ∩ system inspector on Access (Slice 4), and sandbox runs in Agent Studio (Test Lab, Slice 3).
