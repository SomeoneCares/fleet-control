---
name: Autonomous Runtime Control Plane
colors:
  surface: '#f8f9ff'
  surface-dim: '#cbdbf5'
  surface-bright: '#f8f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#eff4ff'
  surface-container: '#e5eeff'
  surface-container-high: '#dce9ff'
  surface-container-highest: '#d3e4fe'
  on-surface: '#0b1c30'
  on-surface-variant: '#3f4850'
  inverse-surface: '#213145'
  inverse-on-surface: '#eaf1ff'
  outline: '#707881'
  outline-variant: '#bfc7d2'
  surface-tint: '#006398'
  primary: '#006194'
  on-primary: '#ffffff'
  primary-container: '#007bb9'
  on-primary-container: '#fdfcff'
  inverse-primary: '#93ccff'
  secondary: '#4648d4'
  on-secondary: '#ffffff'
  secondary-container: '#6063ee'
  on-secondary-container: '#fffbff'
  tertiary: '#545c72'
  on-tertiary: '#ffffff'
  tertiary-container: '#6c748b'
  on-tertiary-container: '#fefcff'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#cce5ff'
  primary-fixed-dim: '#93ccff'
  on-primary-fixed: '#001d31'
  on-primary-fixed-variant: '#004b73'
  secondary-fixed: '#e1e0ff'
  secondary-fixed-dim: '#c0c1ff'
  on-secondary-fixed: '#07006c'
  on-secondary-fixed-variant: '#2f2ebe'
  tertiary-fixed: '#dae2fd'
  tertiary-fixed-dim: '#bec6e0'
  on-tertiary-fixed: '#131b2e'
  on-tertiary-fixed-variant: '#3f465c'
  background: '#f8f9ff'
  on-background: '#0b1c30'
  surface-variant: '#d3e4fe'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '700'
    lineHeight: 40px
    letterSpacing: -0.025em
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
    letterSpacing: -0.02em
  headline-md:
    fontFamily: Inter
    fontSize: 18px
    fontWeight: '600'
    lineHeight: 26px
    letterSpacing: -0.015em
  headline-sm:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '600'
    lineHeight: 22px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Inter
    fontSize: 15px
    fontWeight: '400'
    lineHeight: 22px
  body-md:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  label-lg:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '600'
    lineHeight: 16px
    letterSpacing: 0.02em
  label-md:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 14px
    letterSpacing: 0.01em
  code-lg:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  code-md:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '500'
    lineHeight: 16px
  code-sm:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '400'
    lineHeight: 14px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.75rem
  gutter-desktop: 1rem
  margin: 1rem
  margin-desktop: 1.5rem
  space-xs: 0.25rem
  space-sm: 0.375rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system establishes a high-density, mission-critical instrumentation aesthetic tailored for enterprise operators managing autonomous AI swarms, node clusters, and execution agents. The interface balances high-throughput data visualization with zero-latency visual ergonomics. 

Rather than leaning into dark sci-fi tropes, the design operates strictly in a calibrated daylight environment: crisp, architectural light-mode surfaces inspired by aerospace telemetry interfaces, semiconductor test suites, and surgical control software. The tone is authoritative, hyper-rational, uncompromisingly precise, and structurally grounded. It minimizes visual noise to allow operators to discern anomalies, latency degradation, and execution bottlenecks at a glance.

## Colors

The color architecture enforces strict functional semantics. Pure aesthetic tinting is suppressed; every chromatic signal communicates operational state, autonomy tier, or interactive affordance.

- **Canvas Foundation**: The global backdrop alternates between `#f8fafc` (root canvas) and `#f1f5f9` (structural tracks and nested wells), while active interactive surfaces and telemetry modules reside on `#ffffff`.
- **Primary Telemetry Accent (`#0284c7`)**: Directs user attention to primary control inputs, active routing vectors, and baseline system orchestration.
- **Node & Agent Orchestration (`#6366f1`)**: Encodes autonomous cognitive workflows, inference execution tiers, and synthetic sub-agent spawning events.
- **Operational Status Roles**:
  - **Verified / Nominal**: Emerald (`#10b981`) indicates optimal inference, pass assertions, and stable health.
  - **Human-in-the-Loop / Degraded**: Amber (`#f59e0b`) flags latency spikes, heuristic divergence, and operator review thresholds.
  - **Critical Failure / Assertion Breach**: Rose (`#ef4444`) signals catastrophic termination, node collapse, and failed assertions.
  - **Queued / Suspended**: Slate (`#64748b`) marks idle processes, offline workers, and drained memory segments.
- **Typography & Structural Contrast**: Hierarchy flows from deep slate `#0f172a` (primary telemetry & headers) to `#334155` (operational context), `#64748b` (metadata & metrics keys), down to `#94a3b8` (inactive constraints).

## Typography

The typographic system is optimized for extreme spatial density and split-second legibility. 

- **Primary Interface (Inter)**: Handles all primary navigational items, contextual titles, metrics descriptions, and control labels. Numeric data utilizes tabular lining figures (`font-variant-numeric: tabular-nums`) across all instances to prevent layout jitter during live metric streaming.
- **Telemetry & Machine Output (JetBrains Mono)**: Exclusively designated for UUIDs, runtime parameters, token burn rates, memory addresses, latency traces, raw JSON payloads, and assertion states. This sharp visual split ensures human-authored UI elements are instantly distinct from machine state.
- **Vertical Rhythm**: Font sizes stay constrained between 10px and 32px. Desktop viewports prioritize sub-14px sizes to maximize operational context within a single viewport without horizontal or vertical paging.

## Layout & Spacing

This design system employs an instrumentation grid designed for edge-to-edge data density. 

- **Layout Structure**: Utilizes a 12-column variable grid on desktop with condensed gutters (`16px`) and margins (`24px`), compacting to a flexible 4-column flow on viewports under 768px. Multi-panel split panes (e.g., node list, live visual canvas, inspector drawer) use dedicated continuous-docking models with zero-margin snap borders.
- **Component Padding Scale**: Internal component padding operates on an aggressive 4px/6px base scale. Standard input heights are locked at 28px (compact) and 32px (default) to support deep tree hierarchies and multi-row telemetry tables without vertical sprawl.
- **Reflow Rules**: At lower resolutions, real-time node graphs and terminal panes switch from side-by-side docking to stacked viewports with persistent tabbed switching, guaranteeing that status assertions and telemetry lines are never truncated or obscured.

## Elevation & Depth

Visual hierarchy is maintained through high-contrast tonal layering and structural micro-borders rather than heavy ambient drop shadows. This preserves visual acuity across dense matrix screens.

- **Surface Tiers**:
  - **Base Layer (`#f8fafc`)**: Global application background and non-interactive canvas.
  - **Docking Panes & Cards (`#ffffff`)**: Active work surfaces, node tables, and terminal panels.
  - **Inset Wells (`#f1f5f9`)**: Search toolbars, filter segments, nested log consoles, and data table headers.
- **Edge Definition (Borders)**: Surfaces rely on a 1px razor-sharp boundary (`#e2e8f0` default; `#cbd5e1` on hover or interactive focus). This boundary provides structural containment with zero fuzziness.
- **Shadow System**: Heavy drop shadows are banned. A singular micro-elevation token exists solely for elevated contextual popovers and command palettes:
  `box-shadow: 0 1px 2px 0 rgba(15, 23, 42, 0.05), 0 4px 12px 0 rgba(15, 23, 42, 0.08);`
- **Modal Overlays**: Backdrop scrims use an engineered high-opacity blur (`rgba(15, 23, 42, 0.25)` with `backdrop-filter: blur(2px)`) to focus operator attention during emergency stop procedures or cluster reconfiguration.

## Shapes

The interface embraces a disciplined, engineering-grade shape language. Border radii are tightly constrained to communicate technical precision and industrial durability.

- **Base Radius (`roundedness: 1` / 4px)**: Applied to all standard control inputs, metrics tiles, status chips, segmented controls, and nested sub-panels.
- **Container Radius (`6px`)**: Reserved strictly for top-level cards, data grid containers, dynamic canvas panels, and floating modal sheets.
- **Pills / Circles**: Restricted entirely to operational state indicator pips (6px solid circular indicators) and avatar initials. Interactive buttons and tags never use pill radii, preserving the squared-off instrumentation ethos.

## Components

### Buttons
- **Primary**: Solid deep tech cyan (`#0284c7`), text `#ffffff`, height 30px, horizontal padding 10px, radius 4px. Subtle top-edge inner highlight (`inset 0 1px 0 rgba(255, 255, 255, 0.2)`).
- **Secondary / Ghost**: White background (`#ffffff`), 1px solid `#e2e8f0`, text `#1e293b`. Hover: background `#f8fafc`, border `#cbd5e1`.
- **Destructive / E-Stop**: Solid `#ef4444` or framed border `#ef4444` with `#ef4444` text for emergency agent isolation and immediate cluster spin-down.

### Status Chips & Assertion Tags
- Compact tags: Height 20px, font `JetBrains Mono` 10px, font-weight 500, radius 3px.
- Encoded with 1px hairline tint borders and low-opacity fills:
  - Nominal: Fills `rgba(16, 185, 129, 0.08)`, border `rgba(16, 185, 129, 0.25)`, text `#047857`.
  - Amber Review: Fills `rgba(245, 158, 11, 0.08)`, border `rgba(245, 158, 11, 0.25)`, text `#b45309`.
  - Failed Assertion: Fills `rgba(239, 68, 68, 0.08)`, border `rgba(239, 68, 68, 0.25)`, text `#b91c1c`.
  - Idle / Drained: Fills `rgba(100, 116, 139, 0.08)`, border `rgba(100, 116, 139, 0.25)`, text `#475569`.

### Data Grids & Node Lists
- Multi-row telemetry tables feature locked 32px row heights, fixed headers over `#f1f5f9`, and thin `#f1f5f9` horizontal dividers.
- Hovering a row engages a clean `#f8fafc` background transition with zero vertical layout displacement.
- Monospaced cell values use `JetBrains Mono` with uniform column widths and right-aligned numeric metrics.

### Form Inputs & Terminal Selectors
- Text fields: Height 30px, background `#ffffff`, 1px solid border `#cbd5e1`, typography `Inter` 12px.
- Focus state: Border color transitions to `#0284c7` with a non-diffuse focus ring `0 0 0 1px #0284c7`.
- Monospaced Filter & Query Bars: Embedded leading prefix indicators (e.g., `agent:`, `cluster:`, `assert:`) in `#64748b` with code-completion popovers docked flush to the input frame.

### Instrumentation Cards & Telemetry Panels
- Structure: White `#ffffff` ground, bounded by a 1px `#e2e8f0` border, `padding: 12px`.
- Headers: Micro-labeled titles in `Inter` 11px uppercase (`letterSpacing: 0.05em`) in `#64748b` paired with a secondary live telemetry pip (pulsing 6px dot).

### Agent Execution Graph Nodes
- Graph Canvas: Slate-tinted matrix grid (dots spaced 16px on `#f8fafc`).
- Canvas Nodes: Compact rectangular containers (min-width 160px) bordered by `#cbd5e1` with active execution states indicated via an Indigo `#6366f1` 2px left-side active border accent.