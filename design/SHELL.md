# Fleet Control — shared shell & conventions (read fully before writing an artboard)

Product name on screens: **Fleet Control** with the descriptor "for Hermes Agent" (never "Hermes Fleet Control").
Persona: Dana Whitfield, Fleet Architect. Org: Meridian Bank (fictional). Fleet in focus: "AML Investigation" blueprint v3.
Dates are September 2026. Model names: "Claude Sonnet", "GPT-5", "Llama 4 (local)". Never invent cryptographic/HSM/hallucination-proof claims.

## Frame
Every artboard is a 1440-wide desktop frame. Root: `<div style="width:1440px;min-height:960px;background:#f8f9ff;font-family:Inter,system-ui,sans-serif;color:#0b1c30;display:flex;flex-direction:column">`.
Set the artboard's canvas.json `h` to the real content height (+5%). Content area is `display:flex` row: sidebar 232px + main flex 1.

## Tokens (from the client's DESIGN.md — use these literally, no others)
- bg surface #f8f9ff · container-low #eff4ff · container #e5eeff · container-high #dce9ff · white #ffffff
- text #0b1c30 · text-secondary #3f4850 · outline #707881 · border #bfc7d2 (use #dbe3ee for hairlines inside cards)
- primary #006194 · primary-hover #007bb9 · primary-tint #cce5ff · on-primary #ffffff
- secondary (accent for AI/agents) #4648d4 · secondary-tint #e1e0ff
- tertiary #545c72 · tertiary-tint #dae2fd
- error #ba1a1a · error-tint #ffdad6 · on-error-tint #93000a
- success #1b6b3a · success-tint #d7f2e0 · warning #8a5a00 · warning-tint #fff1cc
- Font: Inter (400/500/600/700). Data/IDs: 'JetBrains Mono', monospace at 12px.
- Type ramp: page title 24/600 (-0.02em) · section 15/600 · body 14/400 lh 20 · small 12/400 lh 16 · label 11/600 uppercase tracking .06em color #3f4850
- Radius: cards 10px, controls 8px, chips 999px. Card: white bg, 1px solid #dbe3ee, no shadow. Control height 36px. Sidebar item 34px.

Put the Google Fonts link inside <helmet>:
`<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap">`
And in <helmet><style>: `body{margin:0} a{color:#006194} a:hover{color:#007bb9} *{box-sizing:border-box}`

## Density rules (this is the point of the redesign)
- One page title, one sentence of context max, ONE primary button (filled #006194) top-right; secondary buttons are outlined (1px #bfc7d2, white).
- At most 4 KPI tiles on a page, and only if they change a decision. No tickers, no log streams unless the screen is about logs.
- Every list screen has a designed empty state where relevant. Every action that mutates Hermes shows "Plan → Apply" language, never "Deploy".
- Status chips: Healthy (success-tint), Degraded (warning-tint), Offline (error-tint), Unknown (container-high). Assurance verdicts are exactly: **Evidence found** (success), **No evidence** (error), **Not verifiable** (container-high, text #3f4850), **Policy blocked** (warning).
- Icons: inline stroke SVG 16px, stroke #3f4850 (or currentColor), stroke-width 1.6, round caps. No emoji.
- Layout with flex/grid + gap. Inline styles on everything the client might restyle. Close every tag, quote every attribute.
- No `{{ }}` holes, no data-dc-script: artboards are STATIC mockups.

## Shell markup (copy exactly; set the active nav item by swapping the two style strings)
Active item style: `background:#cce5ff;color:#006194;font-weight:600`
Inactive item style: `color:#0b1c30;font-weight:500`

See shell-template.html for the full markup. Replace `<!-- MAIN CONTENT -->` with the screen. Keep the top bar text identical across screens (environment chip "Production · eu-west-1", fleet chip "AML Investigation v3").
