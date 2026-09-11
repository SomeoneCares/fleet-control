# Fleet Control for Hermes Agent

Independent control plane around Hermes Agent: design fleets as versioned blueprints, plan and apply them safely, test and verify what agents actually did, detect drift, and give business users a governed workspace for outcomes and decisions.

## Layout

- `docs/build-document.md` — the build plan: decisions, IA, blueprint schema, adapter, release slices, stack.
- `docs/spike-addendum-hermes-0.21.2.md` — findings from reading Hermes 0.21.2 source; supersedes the build document's adapter section (Fleet Control Agent architecture).
- `docs/spike-probes-raw.md` — raw log of the in-process API server probe.
- `docs/research/` — the original deep-research validation and components/features inventory.
- `design/` — the 23-screen redesign. `screens/*.content.html` are the per-screen sources; `build.py <Name> <nav> [template]` assembles a `.dc.html` from `shell-template.html` (admin) or `shell-workspace-template.html` (business users); `shot.py` renders a screenshot with Playwright; `canvas.json` lays the artboards out. `review/` holds rendered previews. `DESIGN.md` is the token source.

The design canvas is published as the "Fleet Control Redesign" artifact on claude.ai.

## Regenerating a screen

```bash
cd design
python3 build.py Assurance assurance
python3 shot.py Assurance     # needs playwright + chromium
```
