# apps/web

React + TypeScript (Vite, Tailwind v4) client for Fleet Control: the admin portal and the business-user Workspace in
one app. What a person sees follows their role: Admins, Fleet Architects and Operators get the admin portal;
Approvers and Viewers land on the Workspace. Buttons follow the role, and a missing one says why.

## Screens

| Area | Screens |
|---|---|
| Design | **Fleet Architect** (mission → proposal → accepted agents as a draft) · **Fleet Designer** (topology from the workflow, with an inspector) · **Agent Studio** (managed fields; applied versions are immutable, so saving makes a draft) · **Workflows** (each workflow, where it can run, the Run window with executor choice and readiness warnings) and a **workflow run** page (steps, artifacts, gates) |
| Operate | **Decision Rooms** (list, and a room: evidence rail · what the fleet found · decision rail, plus its goAML panel) · **goAML reports** · **Ask the fleet** · **Assurance** (claims with their verdicts, 24 h summary, CSV) · **Test Lab** (agent and workflow tests, runs, stop and delete) |
| Estate | **Instances** (Connect drawer, capability report, import, a blueprint from what runs there) · plan and **Drift** resolution · **Integrations** (MCP servers and model providers, tools, who may use them, per-profile health and login expiry) · **Messaging** (channels, delivery rules, test message; hidden when switched off) |
| Govern | **Access** (people and roles, and the inspector: person ∩ agent ∩ system, each line with its rule) · **Audit log** (filters, CSV) |
| Library | **Blueprints** (library, versions, YAML import, plans) · **Content** (zones and files) · **Fleet outputs** |
| Workspace | **Home** · **My decisions** (rooms and workflow gates waiting for me, decided by me) · Decision Rooms · Fleet outputs · Ask the fleet · goAML reports |
| Settings | General · Observability · Approvals · API tokens · Notifications (each person's own) · goAML (the bank's reporting-entity profile and the FIU schema) |

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
and Lena are Approvers, Sam is an Operator, Riya a Viewer), instances with simulated agents, the example AML blueprint
applied to staging with drift to resolve, rooms, outputs, and a production plan waiting for approvals.

## Design tokens

Colours, the type ramp, radii and control sizes come from `design/DESIGN.md` and `design/SHELL.md`.
`scripts/tokens.mjs` turns them into `src/styles/tokens.css` (a Tailwind v4 `@theme`); `npm run dev` regenerates it
and `npm run build` fails if the committed file is stale. Do not hand-edit it or copy colours into components.

## Checks

`npm test` (Vitest: view logic and the token generator) · `npm run typecheck` · `npm run build` (tokens check,
typecheck, production bundle). CI runs all three in the `web` job.

## Layout

- `src/api/client.ts`: typed client for `/api/v1`; shapes mirror `apps/api/fleetcontrol_api/main.py`
- `src/lib/*.ts`: pure view logic per area (`view`, `topology`, `testlab`, `integrations`, `rooms`, `workflows`,
  `messaging`, `access`, `goaml`, …), each with its Vitest file; `soul.ts` mirrors `Soul.render()`
- `src/components/`: the shell (navigation from build document §3.1, `Shell.tsx`) and the small UI kit (`ui.tsx`)
- `src/screens/`: one file per screen or area

Not yet: single sign-on (OIDC), discovery for API-only instances, restoring a snapshot from the UI, sandbox runs in
Agent Studio.
