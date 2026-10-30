import React, { useCallback, useEffect, useRef, useState } from "react"

import {
  api,
  ApiError,
  type BuilderEvent,
  type GraphCompletions,
  type GraphSummary,
  type GraphView,
  type PageState,
  type Recognition,
  type SchemaBundle,
  type SessionSummary,
} from "./api"
import { GraphCanvas } from "./components/GraphCanvas"
import { Inspector } from "./components/Inspector"
import { LeftSidebar } from "./components/LeftSidebar"
import { Player } from "./components/Player"
import { Badge, Icon, cls } from "./components/ui"

interface Toast { msg: string; errors: string[]; kind: "error" | "notice" }

export default function App() {
  const [graphs, setGraphs] = useState<GraphSummary[]>([])
  const [view, setView] = useState<GraphView | null>(null)
  const [selected, setSelected] = useState<string | undefined>()
  const [session, setSession] = useState<SessionSummary | null>(null)
  const [events, setEvents] = useState<BuilderEvent[]>([])
  const [page, setPage] = useState<{ render: string; state: PageState } | null>(null)
  const [recognized, setRecognized] = useState<Record<string, Recognition> | undefined>()
  const [busy, setBusy] = useState(false)
  const [toast, setToast] = useState<Toast | null>(null)
  const [health, setHealth] = useState<{ headed: boolean } | null>(null)
  const [schema, setSchema] = useState<SchemaBundle | null>(null)
  const [completions, setCompletions] = useState<GraphCompletions | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [credential, setCredential] = useState("")
  const [fast, setFast] = useState(true)
  const ws = useRef<WebSocket | null>(null)
  const stoppedSessions = useRef(new Set<string>())
  const right = useSidebar("right", 520)
  const left = useSidebar("left", 300)

  const fail = useCallback((msg: string, errors: string[] = []) => {
    setToast({ msg, errors, kind: "error" })
    window.setTimeout(() => setToast(null), 8000)
  }, [])
  const notice = useCallback((msg: string) => {
    setToast({ msg, errors: [], kind: "notice" })
    window.setTimeout(() => setToast(null), 6000)
  }, [])

  const loadGraphs = useCallback(async () => {
    try {
      const list = await api.graphs.list()
      setGraphs(list)
      return list
    } catch (e) {
      fail(`API unreachable: ${(e as Error).message}`)
      return []
    }
  }, [fail])

  const openGraph = useCallback(
    async (name: string) => {
      try {
        setView(await api.graphs.get(name))
        setSelected(undefined)
        setValues({})
      } catch (e) {
        fail((e as ApiError).message, (e as ApiError).errors)
      }
    },
    [fail],
  )

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null))
    api.schema().then(setSchema).catch(() => setSchema(null))
    loadGraphs().then((list) => {
      const fromHash = location.hash.replace(/^#/, "")
      const first = list.find((g) => g.name === fromHash) ?? list[0]
      if (first) void openGraph(first.name)
    })
  }, [loadGraphs, openGraph])

  useEffect(() => {
    if (!view) return
    location.hash = view.name
    api.graphs.completions(view.name).then(setCompletions).catch(() => setCompletions(null))
    if (!schema) api.schema().then(setSchema).catch(() => setSchema(null))
  }, [view, schema])

  // -- session plumbing --

  const refreshSession = useCallback(async (id: string) => {
    const [s, st, rec] = await Promise.all([
      api.sessions.get(id),
      api.sessions.state(id).catch(() => null),
      api.sessions.recognize(id).catch(() => undefined),
    ])
    if (stoppedSessions.current.has(id)) return
    setSession(s)
    if (st) setPage(st)
    setRecognized(rec)
  }, [])

  const attach = useCallback((id: string) => {
    ws.current?.close()
    setEvents([])
    const sock = new WebSocket(api.sessions.wsUrl(id))
    sock.onmessage = (m) => {
      if (ws.current !== sock) return
      const ev = JSON.parse(m.data) as BuilderEvent
      if (ev.kind === "session_state") {
        const state = ev.session as Partial<SessionSummary>
        setSession(prev => prev?.id === id && state.id === id ? { ...prev, ...state } : prev)
        return
      }
      setEvents((prev) => [...prev, ev])
    }
    sock.onclose = () => {
      if (ws.current === sock) ws.current = null
    }
    ws.current = sock
  }, [])

  const act = useCallback(
    async (fn: () => Promise<unknown>, then?: () => Promise<void>) => {
      setBusy(true)
      try {
        await fn()
        if (then) await then()
      } catch (e) {
        const err = e as ApiError
        fail(err.message ?? String(e), err.errors ?? [])
      } finally {
        setBusy(false)
      }
    },
    [fail],
  )

  const onGraphChanged = useCallback(
    (v: GraphView) => {
      setView(v)
      void loadGraphs()
    },
    [loadGraphs],
  )

  const onGraphDeleted = useCallback(
    async (name: string) => {
      // The API closed any session on that graph.
      if (session?.graph === name) {
        ws.current?.close()
        setSession(null)
        setEvents([])
        setPage(null)
        setRecognized(undefined)
      }
      const list = await loadGraphs()
      notice(`deleted ${name}.yaml`)
      if (list[0]) await openGraph(list[0].name)
      else {
        setView(null)
        location.hash = ""
      }
    },
    [session, loadGraphs, openGraph, notice],
  )

  const sid = session?.status === "stopped" ? undefined : session?.id
  const start = () =>
    view &&
    act(async () => {
      const s = await api.sessions.create(view.name, values, fast, credential)
      setSession(s)
      setPage(null)
      setRecognized(undefined)
      attach(s.id)
      await refreshSession(s.id)
    })
  const drive = (operation: (id: string) => Promise<unknown>) => sid && !view?.lint.length && act(async () => {
    try {
      await operation(sid)
      if (!stoppedSessions.current.has(sid)) await refreshSession(sid)
    } catch (error) {
      if (!stoppedSessions.current.has(sid)) throw error
    }
  })
  const step = () => drive(api.sessions.step)
  const run = () => drive(api.sessions.run)

  // Keyboard: F10 step, F5 run (when a session exists, so the browser's reload is not hijacked otherwise).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!sid) return
      if (e.key === "F10") {
        e.preventDefault()
        step()
      } else if (e.key === "F5") {
        e.preventDefault()
        run()
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  if (!view) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted">
        {graphs.length === 0 ? (
          <div className="space-y-3 text-center">
            <p>No graphs found{health ? "" : " — is the builder API running? (make builder-local)"}.</p>
            <FirstGraph onCreated={(v) => { setView(v); void loadGraphs() }} onError={fail} />
          </div>
        ) : (
          "Loading…"
        )}
      </div>
    )
  }

  // Required inputs the next session still needs: not typed, no default, and not
  // coming from the credential reference.
  const missing = Object.entries(view.workflow.inputs)
    .filter(([n, spec]) => spec.required && !values[n] && (spec.default === undefined || spec.default === null) && !(spec.credential && credential))
    .map(([n]) => n)

  return (
    <div className="flex h-full flex-col bg-canvas">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-outline-variant bg-surface px-3">
        <span className="flex items-center gap-2">
          <span className="flex h-7 w-7 items-center justify-center rounded bg-primary/20 text-primary-soft">
            <Icon name="account_tree" size={18} />
          </span>
          <span className="text-[15px] font-semibold tracking-tight text-on-surface">graphwright builder</span>
        </span>
        <span className="h-5 w-px bg-outline-variant" />
        <nav className="flex min-w-0 items-center gap-1.5 text-[13px]">
          <span className="text-muted">Graphs</span>
          <span className="text-faint">/</span>
          <span className="mono truncate text-on-surface" title={`${view.path}${view.workflow.entry_url ? `\nentry: ${view.workflow.entry_url}` : ""}`}>
            {view.name}
          </span>
          <Badge tone="primary">v{view.workflow.version}</Badge>
          <Badge tone="zinc">{view.workflow.portal}</Badge>
          {view.workflow.risk === "protected" && <Badge tone="warning">protected</Badge>}
        </nav>
        <span className="ml-auto flex items-center gap-2">
          {!schema && (
            <Badge tone="warning" dot title="GET /schema failed — the API predates this UI. Restart it (make builder-local) for schema completions.">
              API outdated
            </Badge>
          )}
          <Badge tone={health ? (health.headed ? "secondary" : "zinc") : "danger"} dot title="browser mode of the API">
            {health ? (health.headed ? "headed" : "headless") : "api offline"}
          </Badge>
        </span>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className="shrink-0 overflow-hidden border-r border-outline-variant bg-surface" style={{ width: left.collapsed ? 0 : left.width }}>
          <LeftSidebar
            graphs={graphs}
            view={view}
            session={session}
            values={values}
            onValuesChange={setValues}
            credential={credential}
            onCredentialChange={setCredential}
            onOpenGraph={(name) => void openGraph(name)}
            onGraphChanged={onGraphChanged}
            onGraphDeleted={(name) => void onGraphDeleted(name)}
            onError={fail}
          />
        </aside>
        <Divider side="left" bar={left} />

        {/* canvas + floating HUDs */}
        <div className="relative min-w-0 flex-1">
          <GraphCanvas
            view={view}
            current={session?.node}
            status={session?.status}
            failedNode={session?.failure?.node}
            visits={session?.visits}
            recognized={recognized}
            selected={selected}
            onSelect={setSelected}
            onConnect={(s, t) => act(async () => onGraphChanged(await api.graphs.addEdge(view.name, s, t)))}
            onDeleteElements={(selection) =>
              act(async () => {
                const updated = await api.graphs.deleteElements(view.name, selection)
                onGraphChanged(updated)
                if (selected && selection.nodes.includes(selected)) setSelected(undefined)
              })
            }
          />
          <div className="pointer-events-none absolute inset-x-0 top-4 flex justify-center">
            <Player
              view={view}
              session={session}
              busy={busy}
              selectedNode={selected}
              missingInputs={missing}
              fast={fast}
              onToggleFast={() => setFast((f) => !f)}
              onStart={() => void start()}
              onStep={() => void step()}
              onRun={() => void run()}
              onObserve={() => sid && act(() => api.sessions.observe(sid), () => refreshSession(sid))}
              onPatch={() => sid && act(() => api.sessions.patch(sid), () => refreshSession(sid))}
              onReset={(node) => sid && act(() => api.sessions.reset(sid, node), () => refreshSession(sid))}
              onClose={() =>
                sid &&
                act(async () => {
                  // Read the last observed state and complete log before releasing the browser.
                  await refreshSession(sid).catch(() => undefined)
                  const log = await api.sessions.events(sid).catch(() => null)
                  if (log) setEvents(log)
                  stoppedSessions.current.add(sid)
                  await api.sessions.close(sid)
                  ws.current?.close()
                  ws.current = null
                  setSession(prev => prev ? { ...prev, status: "stopped", busy: false } : prev)
                })
              }
            />
          </div>
          {session?.failure && (
            <div className="hud pointer-events-auto absolute bottom-4 left-1/2 flex max-w-xl -translate-x-1/2 items-start gap-2 border-danger/40 px-3 py-2 text-xs text-danger-soft">
              <Icon name="error" size={16} className="mt-0.5 shrink-0" />
              <span>
                <b>{session.failure.type}</b> at <span className="mono">{session.failure.node}</span>: {session.failure.message}
                <span className="text-muted"> — details in the Events tab</span>
              </span>
            </div>
          )}
          {session?.result && (
            <div className="hud pointer-events-auto absolute bottom-4 left-1/2 max-w-xl -translate-x-1/2 border-success/40 px-3 py-2 text-xs text-success">
              <span className="mono">{JSON.stringify(session.result)}</span>
            </div>
          )}
        </div>

        <Divider side="right" bar={right} />
        <aside className="shrink-0 overflow-hidden border-l border-outline-variant bg-surface" style={{ width: right.collapsed ? 0 : right.width }}>
          <Inspector
            view={view}
            selected={selected}
            sessionId={sid}
            session={session}
            values={values}
            credential={credential}
            events={events}
            page={page}
            schema={schema}
            completions={completions}
            onGraphChanged={onGraphChanged}
            onError={fail}
            onNotice={notice}
          />
        </aside>
      </div>

      {toast && (
        <div className={`hud fixed bottom-4 left-4 z-50 max-w-xl p-3 text-xs ${toast.kind === "error" ? "border-danger/40 text-danger-soft" : "border-success/40 text-success"}`}>
          <div className="mono whitespace-pre-wrap font-medium">{toast.msg}</div>
          {toast.errors.map((e, i) => (
            <div key={i} className="mono mt-1">• {e}</div>
          ))}
        </div>
      )}
    </div>
  )
}

// --- sidebars ----------------------------------------------------------------------------------

const SIDEBAR_MIN = 280
const SIDEBAR_MAX = 1100

interface SidebarState {
  width: number
  collapsed: boolean
  dragging: boolean
  startDrag: (e: React.MouseEvent) => void
  toggle: () => void
}

function useSidebar(side: "left" | "right", defaultWidth: number): SidebarState {
  const key = `builder.sidebar.${side}`
  const [width, setWidth] = useState<number>(() => {
    try {
      const v = Number(localStorage.getItem(`${key}.width`))
      return v >= SIDEBAR_MIN && v <= SIDEBAR_MAX ? v : defaultWidth
    } catch {
      return defaultWidth
    }
  })
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(`${key}.collapsed`) === "1"
    } catch {
      return false
    }
  })
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    try {
      localStorage.setItem(`${key}.width`, String(width))
      localStorage.setItem(`${key}.collapsed`, collapsed ? "1" : "0")
    } catch {
      /* private mode etc. */
    }
  }, [key, width, collapsed])

  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      setDragging(true)
      const startX = e.clientX
      const startW = collapsed ? SIDEBAR_MIN : width
      const dir = side === "right" ? -1 : 1
      const onMove = (ev: MouseEvent) => {
        const w = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, startW + dir * (ev.clientX - startX)))
        setCollapsed(false)
        setWidth(w)
      }
      const onUp = () => {
        setDragging(false)
        window.removeEventListener("mousemove", onMove)
        window.removeEventListener("mouseup", onUp)
      }
      window.addEventListener("mousemove", onMove)
      window.addEventListener("mouseup", onUp)
    },
    [collapsed, width, side],
  )

  const toggle = useCallback(() => setCollapsed((c) => !c), [])
  return { width, collapsed, dragging, startDrag, toggle }
}

function Divider({ side, bar }: { side: "left" | "right"; bar: SidebarState }) {
  const tabPos = side === "left" ? "-right-3 rounded-r border-l-0" : "-left-3 rounded-l border-r-0"
  const glyph = side === "left" ? (bar.collapsed ? "chevron_right" : "chevron_left") : bar.collapsed ? "chevron_left" : "chevron_right"
  return (
    <div
      className={`relative w-1 shrink-0 cursor-col-resize bg-surface transition-colors hover:bg-primary ${bar.dragging ? "bg-primary" : ""}`}
      onMouseDown={bar.startDrag}
      onDoubleClick={bar.toggle}
      title="drag to resize · double-click to collapse/expand"
    >
      <button
        className={`absolute top-1/2 z-10 flex h-10 w-3 -translate-y-1/2 items-center justify-center border border-outline-variant bg-surface text-muted hover:text-on-surface ${tabPos}`}
        onMouseDown={(e) => e.stopPropagation()}
        onClick={bar.toggle}
        title={bar.collapsed ? "expand" : "collapse"}
      >
        <Icon name={glyph} size={12} />
      </button>
    </div>
  )
}

// --- empty state -----------------------------------------------------------------------------

function FirstGraph({ onCreated, onError }: { onCreated: (v: GraphView) => void; onError: (m: string, e?: string[]) => void }) {
  const [name, setName] = useState("")
  const [portal, setPortal] = useState("")
  const [url, setUrl] = useState("")
  const create = async () => {
    try {
      onCreated(await api.graphs.create(name, portal, url || undefined))
    } catch (e) {
      onError((e as ApiError).message, (e as ApiError).errors)
    }
  }
  return (
    <div className="mx-auto flex w-80 flex-col gap-1.5 text-left">
      <input className={cls.input} placeholder="name — e.g. acme_record_search" value={name} onChange={(e) => setName(e.target.value.replace(/[^a-z0-9_]/gi, "_").toLowerCase())} />
      <input className={cls.input} placeholder="portal — e.g. acme-records" value={portal} onChange={(e) => setPortal(e.target.value)} />
      <input className={cls.input} placeholder="entry url (optional)" value={url} onChange={(e) => setUrl(e.target.value)} />
      <button className={cls.primary} disabled={!name || !portal} onClick={create}>
        Create first graph
      </button>
    </div>
  )
}
