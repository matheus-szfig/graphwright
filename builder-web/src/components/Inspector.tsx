import { useEffect, useRef, useState } from "react"

import {
  api,
  ApiError,
  type BuilderEvent,
  type GraphCompletions,
  type GraphView,
  type PageElement,
  type PageState,
  type RequestCall,
  type SchemaBundle,
  type SessionSummary,
  type TargetReport,
} from "@/api"
import { useDialog } from "./dialog"
import { RequestCallView, RequestsPanel } from "./RequestsPanel"
import { Badge } from "./ui"
import { YamlEditor } from "@/editor/YamlEditor"
import type { EditorContext, SchemaBundle as EditorSchema } from "@/editor/completions"

type Tab = "node" | "graph" | "requests" | "page" | "targets" | "events" | "shot"

export interface InspectorProps {
  view: GraphView
  selected?: string
  sessionId?: string
  session: SessionSummary | null
  values: Record<string, string>
  credential: string
  events: BuilderEvent[]
  page: { render: string; state: PageState } | null
  schema: SchemaBundle | null
  completions: GraphCompletions | null
  onGraphChanged: (v: GraphView) => void
  onError: (msg: string, errors?: string[]) => void
  onNotice: (msg: string) => void
}

export function Inspector(p: InspectorProps) {
  const [tab, setTab] = useState<Tab>("node")
  useEffect(() => {
    if (p.selected) setTab("node")
  }, [p.selected])

  const editorCtx = (root: "workflow" | "node"): EditorContext => ({
    root,
    schema: p.schema as EditorSchema | null,
    graph: p.completions,
    page: p.page?.state ?? null,
  })

  const requestCount = Object.keys(p.view.workflow.requests ?? {}).length
  const tabs: [Tab, string][] = [
    ["node", "Node"],
    ["graph", "Graph YAML"],
    ["requests", `Requests${requestCount ? ` (${requestCount})` : ""}`],
    ["page", "Page"],
    ["targets", "Targets"],
    ["events", `Events${p.events.length ? ` (${p.events.length})` : ""}`],
    ["shot", "Screenshot"],
  ]
  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-4 border-b border-outline-variant px-3">
        {tabs.map(([k, label]) => (
          <button
            key={k}
            onClick={() => setTab(k)}
            className={`-mb-px border-b-2 py-2.5 text-[13px] font-medium transition-colors ${tab === k ? "border-primary-soft text-primary-soft" : "border-transparent text-muted hover:text-on-surface"}`}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {tab === "node" && <NodeEditor {...p} editorCtx={editorCtx("node")} />}
        {tab === "graph" && <GraphEditor {...p} editorCtx={editorCtx("workflow")} />}
        {tab === "requests" && <RequestsPanel view={p.view} session={p.session} values={p.values} credential={p.credential} onError={p.onError} />}
        {tab === "page" && <PageView {...p} />}
        {tab === "targets" && <TargetsView sessionId={p.sessionId} view={p.view} events={p.events} />}
        {tab === "events" && <EventsView events={p.events} />}
        {tab === "shot" && <Screenshot sessionId={p.sessionId} events={p.events} />}
      </div>
    </div>
  )
}

function Hint() {
  return (
    <span className="text-[11px] text-faint">
      <kbd className="rounded border border-outline-variant px-1">Tab</kbd> accepts a suggestion · <kbd className="rounded border border-outline-variant px-1">Ctrl</kbd>+<kbd className="rounded border border-outline-variant px-1">Space</kbd> opens it
      {" "}· suggestions include what the live page shows (ids, roles, names, route)
    </span>
  )
}

type LintState = { status: "idle" } | { status: "checking" } | { status: "ok" } | { status: "errors"; errors: string[] } | { status: "unavailable"; reason: string }

/** A lint line that can be fixed with one click: `… fills 'password_field' with a literal
 * value … value: "{password}"` or `… references unknown input {user}`. */
function inputFix(error: string): { input: string; target?: string } | null {
  const literal = /fills '([^']+)' with a literal value[\s\S]*value: "\{(\w+)\}"/.exec(error)
  if (literal) return { target: literal[1], input: literal[2] }
  const unknown = /unknown input \{(\w+)(?:\.[\w.]+)?\}/.exec(error)
  if (unknown) return { input: unknown[1] }
  return null
}

function LintBox({ lint, onFixInput }: { lint: LintState; onFixInput?: (fix: { input: string; target?: string }) => void }) {
  if (lint.status === "idle") return null
  const cls = {
    checking: "border-outline-variant bg-surface text-muted",
    ok: "border-success/40 bg-success/10 text-success",
    errors: "border-danger/40 bg-danger/10 text-danger-soft",
    unavailable: "border-warning/40 bg-warning/10 text-warning",
  }[lint.status]
  return (
    <div className={`rounded border p-2 text-xs ${cls}`}>
      {lint.status === "checking" && "linting…"}
      {lint.status === "ok" && "lint ok"}
      {lint.status === "errors" &&
        lint.errors.map((l, i) => {
          const fix = onFixInput ? inputFix(l) : null
          return (
            <div key={i} className="flex items-start gap-2">
              <span className="flex-1">• {l}</span>
              {fix && (
                <button
                  className="shrink-0 rounded border border-danger bg-danger/20 px-2 py-0.5 text-[11px] text-danger-soft hover:bg-danger/30"
                  title={fix.target ? `declare input "${fix.input}" and use {${fix.input}} in the fill of ${fix.target}` : `declare input "${fix.input}"`}
                  onClick={() => onFixInput?.(fix)}
                >
                  {fix.target ? `fix: use {${fix.input}}` : `declare {${fix.input}}`}
                </button>
              )}
            </div>
          )
        })}
      {lint.status === "unavailable" && `lint unavailable: ${lint.reason}`}
    </div>
  )
}

/** Replace the `value:` of the fill whose target is `target` with "{input}" — block or
 * flow style. Returns the text unchanged if no such fill is found. */
function templateFillValue(yaml: string, target: string, input: string): string {
  const re = new RegExp(`(target:\\s*${target.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}\\b[\\s\\S]*?value:\\s*)('[^']*'|"[^"]*"|[^,}\\n]*)`)
  return yaml.replace(re, (_, head) => `${head}"{${input}}"`)
}

const PASSWORD_LIKE = /(password|passwd|pwd|secret|token|passphrase)/i
const OTP_LIKE = /(otp|totp|2fa|mfa)/i
const EMAIL_LIKE = /e_?mail/i

/** Debounced lint. `run` is kept in a ref so re-renders caused by anything else (the
 * event stream, screenshots) do not reset the timer — otherwise a busy session could
 * starve the lint forever. */
function useDebouncedLint(run: (text: string) => Promise<string[]>, text: string, enabled: boolean): LintState {
  const [lint, setLint] = useState<LintState>({ status: "idle" })
  const runRef = useRef(run)
  runRef.current = run
  const seq = useRef(0)
  useEffect(() => {
    if (!enabled) {
      setLint({ status: "idle" })
      return
    }
    const id = ++seq.current
    setLint({ status: "checking" })
    const t = window.setTimeout(() => {
      runRef
        .current(text)
        .then((errors) => {
          if (seq.current === id) setLint(errors.length ? { status: "errors", errors } : { status: "ok" })
        })
        .catch((e: ApiError) => {
          if (seq.current !== id) return
          const reason = e.status === 404 ? "endpoint missing — restart the API (make builder-local)" : e.message
          setLint({ status: "unavailable", reason })
        })
    }, 400)
    return () => window.clearTimeout(t)
  }, [text, enabled])
  return lint
}

// --- Node editor -------------------------------------------------------------------------------

function NodeEditor(p: InspectorProps & { editorCtx: EditorContext }) {
  const [yaml, setYaml] = useState("")
  const [dirty, setDirty] = useState(false)
  const [newName, setNewName] = useState("")
  const node = p.selected
  const isTerminal = !!(node && p.view.workflow.nodes[node]?.terminal)

  useEffect(() => {
    if (!node) return
    setNewName(node)
    api.graphs
      .node(p.view.name, node)
      .then((r) => {
        setYaml(r.yaml)
        setDirty(false)
      })
      .catch((e: ApiError) => p.onError(e.message, e.errors))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node, p.view])

  const lint = useDebouncedLint(
    (text) => (node ? api.graphs.lintNode(p.view.name, node, text).then((r) => r.errors) : Promise.resolve([])),
    yaml,
    dirty && !!node,
  )

  if (!node) {
    return <p className="text-sm text-muted">Select a node on the canvas to edit it. Drag between handles to add a transition; Delete removes nodes or edges.</p>
  }
  const isStart = p.view.workflow.start === node
  const wrap = async (fn: () => Promise<GraphView>, notice?: string) => {
    try {
      p.onGraphChanged(await fn())
      if (notice) p.onNotice(notice)
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }
  // One-click fix for the credential lint: declare the input on the workflow (pii) and
  // rewrite this node's fill to use the template. The node is left dirty for you to save.
  const fixInput = async (fix: { input: string; target?: string }) => {
    if (!p.view.workflow.inputs[fix.input]) {
      const type = PASSWORD_LIKE.test(fix.input) ? "password" : OTP_LIKE.test(fix.input) ? "otp" : EMAIL_LIKE.test(fix.input) ? "email" : "string"
      try {
        p.onGraphChanged(await api.graphs.putInput(p.view.name, fix.input, { type, pii: true }))
      } catch (e) {
        p.onError((e as ApiError).message, (e as ApiError).errors)
        return
      }
    }
    if (fix.target) {
      const next = templateFillValue(yaml, fix.target, fix.input)
      if (next !== yaml) {
        setYaml(next)
        setDirty(true)
      }
    }
    p.onNotice(`input {${fix.input}} declared (pii) — fill it in the Inputs panel before you start`)
  }

  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-center gap-2">
        <input
          className="mono flex-1 rounded border border-outline-variant bg-surface px-2 py-1 text-sm"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          onBlur={() => newName && newName !== node && wrap(() => api.graphs.renameNode(p.view.name, node, newName))}
          onKeyDown={(e) => e.key === "Enter" && newName !== node && wrap(() => api.graphs.renameNode(p.view.name, node, newName))}
        />
        {!isStart && (
          <button className="rounded border border-outline-variant px-2 py-1 text-xs hover:bg-surface-high" onClick={() => wrap(() => api.graphs.setStart(p.view.name, node))}>
            set as start
          </button>
        )}
        {!isTerminal && (
          <button
            className="rounded border border-outline-variant px-2 py-1 text-xs hover:bg-surface-high disabled:opacity-40"
            disabled={!p.sessionId || !p.page}
            title={p.sessionId ? "Replace this node's recognize with one derived from the current page (route + distinctive controls)" : "needs a session with an observation"}
            onClick={() => p.sessionId && wrap(() => api.graphs.recognizeFromSession(p.view.name, node, p.sessionId!), `recognize for ${node} generated from the current page — trim it to what matters`)}
          >
            recognize ← page
          </button>
        )}
      </div>
      <YamlEditor
        className="flex-1"
        value={yaml}
        context={p.editorCtx}
        onChange={(v) => {
          setYaml(v)
          setDirty(true)
        }}
      />
      <LintBox lint={lint} onFixInput={fixInput} />
      <div className="flex items-center justify-between gap-2">
        <Hint />
        <div className="flex items-center gap-2">
          <span className="text-xs text-faint">{dirty ? "unsaved" : "saved"}</span>
          <button
            className="whitespace-nowrap rounded bg-primary px-3 py-1 text-xs font-medium text-white hover:brightness-110 disabled:opacity-40"
            disabled={!dirty || lint.status === "errors" || lint.status === "checking"}
            onClick={() => wrap(() => api.graphs.putNode(p.view.name, node, yaml)).then(() => setDirty(false))}
          >
            Save node
          </button>
        </div>
      </div>
    </div>
  )
}

// --- whole-graph YAML --------------------------------------------------------------------------

function GraphEditor(p: InspectorProps & { editorCtx: EditorContext }) {
  const [yaml, setYaml] = useState(p.view.yaml)
  const [dirty, setDirty] = useState(false)
  useEffect(() => {
    setYaml(p.view.yaml)
    setDirty(false)
  }, [p.view])
  const lint = useDebouncedLint((text) => api.graphs.lint(p.view.name, text).then((r) => r.errors), yaml, dirty)

  const save = async () => {
    try {
      p.onGraphChanged(await api.graphs.putYaml(p.view.name, yaml))
      setDirty(false)
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }
  return (
    <div className="flex h-full flex-col gap-2">
      <YamlEditor
        className="flex-1"
        value={yaml}
        context={p.editorCtx}
        onChange={(v) => {
          setYaml(v)
          setDirty(true)
        }}
      />
      <LintBox lint={lint} />
      <div className="flex items-center justify-between gap-2">
        <Hint />
        <button className="whitespace-nowrap rounded bg-primary px-3 py-1 text-xs font-medium text-white hover:brightness-110 disabled:opacity-40" disabled={!dirty || lint.status === "errors" || lint.status === "checking"} onClick={save}>
          Save graph
        </button>
      </div>
    </div>
  )
}

// --- PageState ---------------------------------------------------------------------------------

function PageView(p: InspectorProps) {
  const [raw, setRaw] = useState(true)
  const dialog = useDialog()
  const page = p.page
  if (!page) return <p className="text-sm text-muted">No observation yet — start a session.</p>
  const s = page.state

  const addTarget = async (e: PageElement) => {
    const suggested = (e.name || e.nearby[0] || e.dom_id || e.role)
      .normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 40)
    const name = await dialog.prompt({
      title: "New target",
      message: `From the ${e.role} “${e.name || e.nearby[0] || e.dom_id || e.id}”.`,
      label: "Target name (snake_case)",
      defaultValue: suggested,
      confirmLabel: "Add target",
      transform: (v) => v.replace(/[^a-z0-9_]/gi, "_").toLowerCase(),
      validate: (v) =>
        !v ? "a name is required"
        : !/^[a-z][a-z0-9_]*$/.test(v) ? "start with a letter; a–z, 0–9 and _ only"
        : p.view.workflow.targets[v] ? "a target with this name exists"
        : null,
    })
    if (!name) return
    try {
      const r = await api.graphs.targetFromElement(p.view.name, e, name)
      p.onGraphChanged(r.view)
      p.onNotice(`target "${r.target}" added:\n${r.yaml}`)
    } catch (err) {
      p.onError((err as ApiError).message, (err as ApiError).errors)
    }
  }

  return (
    <div className="space-y-3 text-xs">
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-muted">
        <span>
          <b className="text-on-surface">{s.portal}</b> {s.route}
        </span>
        <span>{s.stats.kept}/{s.stats.candidates} elements</span>
        <span>{s.stats.frames} frame(s)</span>
        <span>{s.stats.total_ms} ms</span>
        <button className="text-primary-soft hover:underline" onClick={() => setRaw(!raw)}>
          {raw ? "show render" : "show elements table"}
        </button>
      </div>
      {!raw ? (
        <pre className="mono whitespace-pre-wrap rounded border border-outline-variant bg-recess p-2 leading-5">{page.render}</pre>
      ) : (
        <table className="mono w-full text-left text-[11px]">
          <thead className="text-muted">
            <tr>
              <th className="pr-2">id</th>
              <th className="pr-2">role</th>
              <th className="pr-2" title="HTML id attribute — usable as dom_id in strategies and recognize">dom_id</th>
              <th className="pr-2">name</th>
              <th className="pr-2">value</th>
              <th className="pr-2">nearby</th>
              <th className="pr-2">landmark</th>
              <th className="pr-2">vp</th>
              <th className="w-6" />
            </tr>
          </thead>
          <tbody>
            {s.elements.map((e) => (
              <tr key={e.id} className="group border-t border-outline-variant align-top hover:bg-surface">
                <td className="whitespace-nowrap pr-2 text-primary-soft">{e.id}</td>
                <td className="whitespace-nowrap pr-2">{e.role}</td>
                <td className="pr-2 text-success">{e.dom_id ?? ""}</td>
                <td className="pr-2">{e.name}</td>
                <td className="pr-2 text-muted">{e.value ?? ""}</td>
                <td className="pr-2 text-muted">{e.nearby.join(" | ")}</td>
                <td className="pr-2 text-faint">{e.landmark ?? ""}</td>
                <td className="pr-2">{e.in_viewport ? "•" : ""}</td>
                <td className="whitespace-nowrap">
                  <button
                    className="inline-flex h-5 w-5 items-center justify-center rounded text-faint hover:bg-primary hover:text-white group-hover:text-on-surface-variant"
                    title={`create a target for ${e.id} (${e.role} “${e.name || e.nearby[0] || e.dom_id || ""}”) — strategies: name → dom_id → nearby`}
                    aria-label="create target"
                    onClick={() => addTarget(e)}
                  >
                    <TargetIcon />
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

/** Crosshair-with-plus, 14px, inherits currentColor (no icon library in this app). */
function TargetIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="7" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
      <path d="M12 9v6M9 12h6" />
    </svg>
  )
}

// --- targets -----------------------------------------------------------------------------------

function TargetsView({ sessionId, view, events }: { sessionId?: string; view: GraphView; events: BuilderEvent[] }) {
  const [report, setReport] = useState<Record<string, TargetReport> | null>(null)
  useEffect(() => {
    if (!sessionId) return
    api.sessions.targets(sessionId).then(setReport).catch(() => setReport(null))
  }, [sessionId, view, events.length])
  if (!sessionId) return <p className="text-sm text-muted">Targets resolve against the session's last observation — start one.</p>
  if (!report) return <p className="text-sm text-muted">Loading…</p>
  return (
    <div className="space-y-2 text-xs">
      {Object.entries(report).map(([name, r]) => (
        <details key={name} className="rounded border border-outline-variant bg-recess p-2" open={!r.ok}>
          <summary className="cursor-pointer">
            <span className={`mr-2 inline-block h-2 w-2 rounded-full ${r.ok ? "bg-success" : "bg-danger"}`} />
            <span className="mono font-semibold">{name}</span>
            <span className="ml-2 text-muted">{r.description}</span>
            {r.element && (
              <span className="mono ml-2 text-primary-soft">
                → {r.element.id} {r.element.role} “{r.element.name}”
              </span>
            )}
          </summary>
          <ul className="mono mt-2 space-y-0.5 text-muted">
            {r.attempts.map((a, i) => (
              <li key={i}>{a}</li>
            ))}
          </ul>
        </details>
      ))}
    </div>
  )
}

// --- events ------------------------------------------------------------------------------------

const EVENT_COLOR: Record<string, string> = {
  failure: "text-danger-soft",
  on_fail: "text-warning",
  request: "text-secondary-soft",
  move: "text-primary-soft",
  terminal: "text-success",
  transitions: "text-warning",
  recognize: "text-on-surface",
  resync: "text-tertiary-soft",
}

function EventsView({ events }: { events: BuilderEvent[] }) {
  if (!events.length) return <p className="text-sm text-muted">No events yet.</p>
  return (
    <div className="mono space-y-1 text-[11px]">
      {[...events].reverse().map((e, i) => {
        const { kind, node, ts, render, ...rest } = e
        void render
        if (kind === "request") return <RequestEvent key={`${ts}-${i}`} node={node} call={rest as unknown as RequestCall} />
        if (kind === "on_fail") return <OnFailEvent key={`${ts}-${i}`} node={node} to={String(rest.to ?? "")} error={rest.error as Record<string, unknown> | undefined} />
        const raw = (rest.reasons ?? rest.trace ?? rest.attempts ?? rest.details) as unknown[] | undefined
        const lines = raw?.map((l) => (typeof l === "string" ? l : JSON.stringify(l)))
        const brief = Object.entries(rest)
          .filter(([k]) => !["reasons", "trace", "attempts", "details", "spec", "matches"].includes(k))
          .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
          .join("  ")
        return (
          <details key={`${ts}-${i}`} className="rounded border border-outline-variant bg-recess px-2 py-1" open={kind === "failure"}>
            <summary className="cursor-pointer">
              <span className={EVENT_COLOR[kind] ?? "text-on-surface-variant"}>[{kind}]</span> <span className="text-faint">{node}</span>{" "}
              <span className="text-muted">{brief.slice(0, 160)}</span>
            </summary>
            {lines && <pre className="mt-1 whitespace-pre-wrap text-muted">{lines.join("\n")}</pre>}
            {rest.spec ? <pre className="mt-1 text-faint">{JSON.stringify(rest.spec)}</pre> : null}
          </details>
        )
      })}
    </div>
  )
}

function RequestEvent({ node, call }: { node: string | null; call: RequestCall }) {
  return (
    <details className="rounded border border-outline-variant bg-recess px-2 py-1" open={!call.ok}>
      <summary className="cursor-pointer">
        <span className={EVENT_COLOR.request}>[request]</span> <span className="text-faint">{node}</span>{" "}
        <span className="text-on-surface">{call.name}</span>{" "}
        <Badge tone={call.ok ? "success" : "danger"}>{call.ok ? "ok" : call.code ?? "failed"}</Badge>{" "}
        <span className="text-muted">
          {call.attempts.length} attempt{call.attempts.length === 1 ? "" : "s"}
          {call.attempts.length ? ` · ${call.attempts.reduce((n, a) => n + a.elapsed_ms, 0)} ms` : ""}
          {call.error ? ` · ${call.error}` : ""}
        </span>
      </summary>
      <div className="mt-1.5 pb-1">
        <RequestCallView call={call} compact />
      </div>
    </details>
  )
}

function OnFailEvent({ node, to, error }: { node: string | null; to: string; error?: Record<string, unknown> }) {
  const details = (error?.details as string[] | undefined) ?? []
  return (
    <details className="rounded border border-warning/40 bg-warning/5 px-2 py-1" open>
      <summary className="cursor-pointer">
        <span className={EVENT_COLOR.on_fail}>[on_fail]</span> <span className="text-faint">{node}</span>{" "}
        <span className="text-on-surface">→ {to}</span>{" "}
        <span className="text-muted">
          {String(error?.type ?? "")}: {String(error?.message ?? "")}
        </span>
      </summary>
      <div className="mt-1 text-muted">
        the error is in <span className="text-on-surface">vars._error</span> ({String(error?.class ?? "")}
        {error?.code ? ` · ${String(error.code)}` : ""}) for {to} to branch on
      </div>
      {details.length > 0 && <pre className="mt-1 whitespace-pre-wrap text-muted">{details.join("\n")}</pre>}
    </details>
  )
}

// --- screenshot --------------------------------------------------------------------------------

function Screenshot({ sessionId, events }: { sessionId?: string; events: BuilderEvent[] }) {
  const [src, setSrc] = useState<string | null>(null)
  const [auto, setAuto] = useState(true)
  const refresh = async () => {
    if (!sessionId) return
    try {
      const r = await api.sessions.screenshot(sessionId)
      setSrc(`data:image/jpeg;base64,${r.jpeg_base64}`)
    } catch {
      /* busy — keep the last one */
    }
  }
  useEffect(() => {
    if (auto) void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, events.length, auto])
  if (!sessionId) return <p className="text-sm text-muted">Start a session to see the page.</p>
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-xs">
        <button className="rounded border border-outline-variant px-2 py-1 hover:bg-surface-high" onClick={refresh}>
          Refresh
        </button>
        <label className="flex items-center gap-1 text-muted">
          <input type="checkbox" checked={auto} onChange={(e) => setAuto(e.target.checked)} /> after each step
        </label>
      </div>
      {src ? <img src={src} alt="page" className="w-full rounded border border-outline-variant" /> : <p className="text-xs text-faint">—</p>}
    </div>
  )
}
