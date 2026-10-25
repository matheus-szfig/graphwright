---
name: Synthetic Flow
colors:
  surface: '#0f131c'
  surface-dim: '#0f131c'
  surface-bright: '#353943'
  surface-container-lowest: '#0a0e17'
  surface-container-low: '#181b25'
  surface-container: '#1c1f29'
  surface-container-high: '#262a34'
  surface-container-highest: '#31353f'
  on-surface: '#dfe2ef'
  on-surface-variant: '#c7c4d7'
  inverse-surface: '#dfe2ef'
  inverse-on-surface: '#2c303a'
  outline: '#908fa0'
  outline-variant: '#464554'
  surface-tint: '#c0c1ff'
  primary: '#c0c1ff'
  on-primary: '#1000a9'
  primary-container: '#8083ff'
  on-primary-container: '#0d0096'
  inverse-primary: '#494bd6'
  secondary: '#4cd7f6'
  on-secondary: '#003640'
  secondary-container: '#03b5d3'
  on-secondary-container: '#00424e'
  tertiary: '#ddb7ff'
  on-tertiary: '#490080'
  tertiary-container: '#b76dff'
  on-tertiary-container: '#400071'
  error: '#ffb4ab'
  on-error: '#690005'
  error-container: '#93000a'
  on-error-container: '#ffdad6'
  primary-fixed: '#e1e0ff'
  primary-fixed-dim: '#c0c1ff'
  on-primary-fixed: '#07006c'
  on-primary-fixed-variant: '#2f2ebe'
  secondary-fixed: '#acedff'
  secondary-fixed-dim: '#4cd7f6'
  on-secondary-fixed: '#001f26'
  on-secondary-fixed-variant: '#004e5c'
  tertiary-fixed: '#f0dbff'
  tertiary-fixed-dim: '#ddb7ff'
  on-tertiary-fixed: '#2c0051'
  on-tertiary-fixed-variant: '#6900b3'
  background: '#0f131c'
  on-background: '#dfe2ef'
  surface-variant: '#31353f'
typography:
  headline-xl:
    fontFamily: Geist
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: -0.02em
  headline-lg:
    fontFamily: Geist
    fontSize: 22px
    fontWeight: '600'
    lineHeight: 30px
    letterSpacing: -0.015em
  headline-md:
    fontFamily: Geist
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
    letterSpacing: -0.01em
  body-lg:
    fontFamily: Geist
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
  body-md:
    fontFamily: Geist
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 18px
  body-sm:
    fontFamily: Geist
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  mono-lg:
    fontFamily: JetBrains Mono
    fontSize: 13px
    fontWeight: '500'
    lineHeight: 18px
  mono-md:
    fontFamily: JetBrains Mono
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 16px
  mono-sm:
    fontFamily: JetBrains Mono
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 14px
    letterSpacing: 0.02em
  label-caps:
    fontFamily: JetBrains Mono
    fontSize: 10px
    fontWeight: '600'
    lineHeight: 12px
    letterSpacing: 0.06em
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 0.75rem
  margin: 1rem
  space-xs: 0.25rem
  space-sm: 0.5rem
  space-md: 0.75rem
  space-lg: 1rem
  space-xl: 1.5rem
---

## Brand & Style

This design system targets enterprise automation engineers, systems architects, and technical operators orchestrating critical Robotic Process Automation (RPA) pipelines. The interface balances high-density information architecture with visual calmness, ensuring users can parse complex directed acyclic graphs (DAGs) without cognitive fatigue.

The aesthetic fuses **Modern Technical Minimalist** and **Precision Instrument Workspace**:
- **Tactile Instrument Quality:** Dark slate surfaces with calibrated contrast levels, 1px subtle boundary definitions, and crisp geometric alignments.
- **Operational Clarity:** High-contrast semantic accents cut through the deep background, communicating node health, trigger types, and real-time execution states instantly.
- **Developer-Native Utility:** Compact typography, monospace precision for inputs/payloads, and floating glass-tinted HUD overlays that maximize the infinite canvas viewport.

## Colors

The palette establishes an ultra-deep technical canvas that recedes into the background, allowing operational node graphs to stand forward.

### Functional Roles & Accents
- **Canvas Base & Paneling:** `#090d16` provides an ink-slate canvas foundation. Panel chrome layers step up to `#0f172a` (surfaces) and `#1e293b` (cards/nodes) to create subtle tonal separation without harsh borders.
- **Active Processing & Flow (Primary & Secondary):** `#6366f1` (Indigo) and `#06b6d4` (Cyan) serve as primary pathways, executing step highlights, dynamic connection splines, and active selection bounding boxes.
- **Triggers & Ingestion (Tertiary):** `#a855f7` (Electric Purple) defines workflow entry points, webhooks, schedule triggers, and external listener nodes.
- **Node Status Semantics:**
  - **Success / Healthy:** `#10b981` (Emerald) denotes finished tasks, successful runs, and verified test assertions.
  - **Warning / Retry / Throttled:** `#f59e0b` (Amber) signals retry backoffs, non-fatal anomalies, and deprecation notices.
  - **Failure / Blocked:** `#f43f5e` (Rose) marks unhandled exceptions, failed tasks, and broken port bindings.
- **Canvas Grid Dots:** `#334155` rendered at 35% opacity against the canvas background.

## Typography

The typography leverages **Geist** for crisp structural UI clarity alongside **JetBrains Mono** for payload variables, schema properties, execution timings, and node connection data types.

- **Dual Font Hierarchy:** Geist manages human-readable navigation, modal headers, documentation text, and action labels. JetBrains Mono takes over for port handles, runtime durations (e.g., `42ms`), JSON payload configurations, and log streams.
- **Density Over Scale:** Headings stay compact (peaking at 28px) to maximize real-estate efficiency in complex multi-pane development environments.
- **Micro-Labels:** Use `label-caps` in uppercase styling with `letter-spacing: 0.06em` for node status tags, port input/output labels, and breadcrumb sections.

## Layout & Spacing

The workspace implements an **Infinite Canvas Core with Pinned Docking Rails**:

- **Left Rail (Node Library / Catalog):** Fixed 280px width, collapsible to a 48px icon ribbon. Organizes triggers, logic gates, AI connectors, and custom scripting nodes.
- **Center Canvas:** Dynamic fluid viewport containing the interactive node-link graph with an infinite pan/zoom canvas.
- **Right Rail (Inspector / Node Config):** Fixed 340px width (expandable to 480px during step debugging), collapsible to full-bleed canvas.
- **Bottom Drawer (Execution Logs / Live Tracer):** Height adjustable (default 240px, minimizable to 36px status bar), rendering streaming tabular log outputs.
- **Floating HUD Overlays:** Suspended with 16px safety padding from viewport edges. Minimap anchors bottom-right above the log drawer; run/pause/step toolbar anchors top-center.
- **Spacing Scale:** Standard 4px baseline rhythm (`0.25rem` step). Node internals adhere to tight 8px-12px padding envelopes to prevent node cards from bloating the DAG canvas.

## Elevation & Depth

This system avoids heavy drop shadows, instead using **Tonal Surface Stacking** coupled with **Subtle Border Glows** to establish hierarchy.

- **Level 0 (Canvas Surface):** `#090d16` flat background with CSS radial-gradient dot pattern (`#334155` at 35% opacity, 20px grid pitch).
- **Level 1 (Docked Panels & Rails):** `#0f172a` backdrop with 1px solid border `#1e293b`.
- **Level 2 (Standard Node Card):** `#1e293b` container surface with 1px solid `#334155`. Upon selection, border shifts to `#6366f1` with an ambient glow of `0 0 0 1px #6366f1, 0 8px 24px -4px rgba(99, 102, 241, 0.25)`.
- **Level 3 (Floating HUDs & Modals):** `#0f172a` tinted at 85% opacity with `backdrop-filter: blur(12px)`, bordered by `rgba(255, 255, 255, 0.08)`, and cast with a soft ambient shadow (`0 12px 32px -4px rgba(0, 0, 0, 0.5)`).
- **Status Edge Accents:** Running nodes pulse a subtle border animation using secondary cyan `rgba(6, 182, 212, 0.4)`, while failed nodes emit a crisp static ring of rose `rgba(244, 63, 94, 0.3)`.

## Shapes

The interface implements a **Soft Industrial Geometry (Level 1)**:

- **Nodes & Containers:** 4px (`rounded`) corner radius. This keeps connected splines visually tangent to node boundaries while maintaining an engineered, precision-instrument feel.
- **Port Handles:** Perfectly circular 8px diameter targets (`rounded-full`) extruded 4px beyond node boundaries with a 2px inner core.
- **Inputs & Action Buttons:** 4px radius for tight spatial packing.
- **Floating Toolbars & Chips:** 6px radius (`rounded-lg`) to gently separate transient or overlay controls from the structural node system.

## Components

### Node Cards
- **Structure:** Encapsulated containers composed of a Header Bar, Status Indicator, Body (parameters/summary), and Edge Handle Ports.
- **Port Design:** Small circular ports positioned along left (inputs) and right (outputs) edges. Hovering a port reveals an indicator ring matching the expected data type schema (e.g., string = cyan, number = amber, trigger = purple).
- **Header Badges:** Displays an icon, node type identifier (`Geist`, semi-bold), and execution latency tag (`JetBrains Mono`, `mono-sm`).

### Canvas Control HUDs (Minimap, Zoom, Run Toolbar)
- **Run/Debug Toolbar:** Floats top-center of the canvas. Contains grouped buttons: Execute Workflow (Emerald CTA), Debug Step-Over, Pause, and Environment Selector dropdown.
- **Minimap:** Anchored bottom-right; semi-transparent slate container displaying a 1:1 scaled down preview of all nodes with interactive viewport lens rect.

### Buttons & Input Fields
- **Primary Action (Run/Deploy):** Solid emerald `#10b981` text with `#064e3b` background or solid `#6366f1` background with crisp white typography.
- **Canvas Tool Buttons:** Minimal `#0f172a` buttons with `#94a3b8` icons, transitioning to `#e2e8f0` with `#1e293b` fill on hover.
- **Inputs & Schema Selectors:** Dark recessed fill `#0b0f19` with a 1px `#1e293b` border. Focus ring applies 1px `#6366f1` with zero fuzzy blur.

### Execution Logs Drawer
- Split-pane layout with search, log-level filter chips (INFO, WARN, ERROR), clear button, and auto-scroll pin toggle.
- Row items show timestamp (`mono-sm`, slate-500), step name (`mono-sm`, indigo-400), duration (`mono-sm`, slate-400), and payload preview with collapsible JSON trees.

### Node Library Palette
- Grouped accordions (Triggers, Core Logic, Integrations, Data Transformation).
- Draggable node tiles featuring left-aligned iconography, semantic color category dot, and grab cursor affordance.

### Inspector / Configuration Panel
- Key-value parameter editor supporting static inputs, dynamic variable pickers (`{{payload.data}}`), and embedded Monaco-style expression editors.