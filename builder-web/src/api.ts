// Thin client for the builder API. Always addressed relative to the app's base so the
// same bundle works behind nginx (/builder/api) and in standalone dev (vite proxy).

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api`

export class ApiError extends Error {
  status: number
  errors: string[]
  constructor(status: number, detail: string, errors: string[] = []) {
    super(detail)
    this.status = status
    this.errors = errors
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  })
  if (res.status === 204) return undefined as T
  const body = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? res.statusText)
    throw new ApiError(res.status, detail, body.errors ?? [])
  }
  return body as T
}

const json = (data: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(data) })

// --- types mirrored from the API -------------------------------------------------------

export interface GraphSummary { name: string; workflow: string; version: number; portal: string; nodes: number; lint_ok: boolean }
export interface Edge { from: string; to: string; label: string }
export interface WorkflowNode {
  intent?: string; terminal?: "success" | "outcome" | "failure"; result?: Record<string, unknown>
  transitions?: { to: string; when?: Record<string, unknown> }[]; actions?: Record<string, unknown>[]
  call?: string; then?: string; max_visits?: number; timeout_s?: number; on_fail?: string
}
export interface InputSpec {
  type: string; required: boolean; pii: boolean; description?: string; default?: unknown; credential?: string | null
}
export interface RequestSpec {
  kind: "http" | "totp" | "imap"; description?: string
  method?: string; url?: string; headers?: Record<string, string>; query?: Record<string, string>; json?: unknown; body?: string
  expect_status?: (number | string)[]; secret?: string; digits?: number; period?: number
  imap?: { host: string; port?: number; username: string; password: string; folder?: string; from?: string; subject?: string; max_age_s?: number }
  timeout_s?: number
  retry?: { attempts?: number; backoff_s?: number[]; on_status?: (number | string)[]; on_network_error?: boolean; until_extracted?: boolean }
  extract?: Record<string, { json?: string; regex?: string; header?: string; required?: boolean }>
  sensitive?: boolean; cache?: "none" | "run"
}
export interface Workflow {
  workflow: string; version: number; portal: string; start: string; entry_url?: string; risk?: "open" | "protected"
  inputs: Record<string, InputSpec>
  requests?: Record<string, RequestSpec>
  nodes: Record<string, WorkflowNode>
  targets: Record<string, { description: string; strategies: Record<string, unknown>[] }>
}
export interface GraphView { name: string; path: string; yaml: string; workflow: Workflow; edges: Edge[]; lint: string[] }

export interface SessionSummary {
  id: string; graph: string; inputs: Record<string, string>; status: string; node: string
  visits: Record<string, number>; events: number; failure: Failure | null; result: Record<string, unknown> | null
  vars: Record<string, unknown>; url: string | null; busy: boolean
  credential?: string | null; requests?: Record<string, "ok" | "failed" | "pending">
}
export interface Failure { class: string; type: string; message: string; details: string[]; node: string; route: string | null }
export interface BuilderEvent { kind: string; node: string | null; ts: string; [k: string]: unknown }
export interface PageElement {
  id: string; frame: number; role: string; tag: string; dom_id: string | null; name: string; value: string | null; href: string | null
  options: string[]; states: string[]; landmark: string | null; nearby: string[]
  bbox: { x: number; y: number; w: number; h: number }; in_viewport: boolean
}
export interface PageState {
  url: string; portal: string; route: string; title: string; page_type: string | null; signature: string | null
  frames: { index: number; url: string; route: string; parent: number | null }[]
  alerts: string[]; headings: { level: number; text: string; frame: number }[]
  elements: PageElement[]; tables: { id: string; name: string | null; headers: string[]; rows: number }[]
  stats: { candidates: number; hidden: number; kept: number; frames: number; extract_ms: number; total_ms: number }
}
export interface TargetReport { ok: boolean; element: PageElement | null; attempts: string[]; description: string }
export interface Recognition { ok: boolean; reasons: string[] }

/** One try of a graph request, as engine/requests.py traces it (secrets masked). */
export interface RequestAttempt {
  n: number; started_at: string; elapsed_ms: number; outcome: "ok" | "retry" | "failed"
  request?: Record<string, unknown>; status?: number; response_headers?: Record<string, string>
  body_excerpt?: string; extracted?: Record<string, string>; missing?: string[]; error?: string; wait_s?: number
}
/** A `request` event: one call of a request with every attempt. */
export interface RequestCall {
  name: string; request_kind: string; ok: boolean; error: string | null; code: string | null
  values: Record<string, unknown>; attempts: RequestAttempt[]
}
export interface RequestReport {
  ok: boolean; request: string; kind?: string; error: string | null; calls: RequestCall[]
  values?: Record<string, unknown>; session?: string
}

// --- graphs --------------------------------------------------------------------------------------

export interface SchemaBundle {
  workflow: unknown; node: unknown; request?: unknown; shapers: string[]; verbs: string[]; condition_kinds: string[]
  strategy_fields: string[]; roles: string[]; request_kinds?: string[]; request_fields?: string[]; extractor_fields?: string[]
}
export interface GraphCompletions {
  nodes: string[]; targets: string[]; inputs: string[]; shapers: string[]; macros: string[]
  requests?: Record<string, string[]>
}

export const api = {
  health: () => req<{ status: string; headed: boolean; graphs_dir: string }>("/health"),
  schema: () => req<SchemaBundle>("/schema"),
  graphs: {
    list: () => req<GraphSummary[]>("/graphs"),
    completions: (name: string) => req<GraphCompletions>(`/graphs/${name}/completions`),
    lintNode: (name: string, node: string, yaml: string) => req<{ ok: boolean; errors: string[] }>(`/graphs/${name}/nodes/${node}/lint`, json({ yaml })),
    targetFromElement: (name: string, element: PageElement, targetName?: string) =>
      req<{ target: string; yaml: string; view: GraphView }>(`/graphs/${name}/targets/from-element`, json({ element, name: targetName })),
    recognizeFromSession: (name: string, node: string, sessionId: string, maxElements = 3) =>
      req<GraphView>(`/graphs/${name}/nodes/${node}/recognize-from-session`, json({ session_id: sessionId, max_elements: maxElements })),
    get: (name: string) => req<GraphView>(`/graphs/${name}`),
    create: (name: string, portal: string, entry_url?: string) => req<GraphView>("/graphs", json({ name, portal, entry_url })),
    delete: (name: string) => req<void>(`/graphs/${name}`, { method: "DELETE" }),
    putYaml: (name: string, yaml: string) => req<GraphView>(`/graphs/${name}`, { method: "PUT", body: JSON.stringify({ yaml }) }),
    lint: (name: string, yaml: string) => req<{ ok: boolean; errors: string[] }>(`/graphs/${name}/lint`, json({ yaml })),
    node: (name: string, node: string) => req<{ name: string; yaml: string; node: WorkflowNode }>(`/graphs/${name}/nodes/${node}`),
    putNode: (name: string, node: string, yaml: string) => req<GraphView>(`/graphs/${name}/nodes/${node}`, { method: "PUT", body: JSON.stringify({ yaml }) }),
    addNode: (name: string, node: string, kind = "step", intent = "") => req<GraphView>(`/graphs/${name}/nodes`, json({ name: node, kind, intent })),
    renameNode: (name: string, node: string, newName: string) => req<GraphView>(`/graphs/${name}/nodes/${node}/rename`, json({ new_name: newName })),
    deleteNode: (name: string, node: string) => req<GraphView>(`/graphs/${name}/nodes/${node}`, { method: "DELETE" }),
    setStart: (name: string, node: string) => req<GraphView>(`/graphs/${name}/start/${node}`, { method: "POST" }),
    putInput: (name: string, input: string, spec: Partial<InputSpec> & { type: string }) =>
      req<GraphView>(`/graphs/${name}/inputs/${input}`, { method: "PUT", body: JSON.stringify(spec) }),
    deleteInput: (name: string, input: string) => req<GraphView>(`/graphs/${name}/inputs/${input}`, { method: "DELETE" }),
    addEdge: (name: string, source: string, target: string, condition?: Record<string, unknown>) =>
      req<GraphView>(`/graphs/${name}/edges`, json({ source, target, condition })),
    deleteEdge: (name: string, source: string, target: string) => req<GraphView>(`/graphs/${name}/edges/${source}/${target}`, { method: "DELETE" }),
    testRequest: (name: string, request: string, body: { inputs: Record<string, string>; vars?: Record<string, unknown>; credential?: string; reveal?: boolean }) =>
      req<RequestReport>(`/graphs/${name}/requests/${request}/test`, json(body)),
  },
  sessions: {
    list: () => req<SessionSummary[]>("/sessions"),
    create: (graph: string, inputs: Record<string, string>, fast: boolean, credential?: string) =>
      req<SessionSummary>("/sessions", json({ graph, inputs, fast, credential: credential || undefined })),
    request: (id: string, request: string, body: { vars?: Record<string, unknown>; reveal?: boolean }) =>
      req<RequestReport>(`/sessions/${id}/requests/${request}`, json(body)),
    get: (id: string) => req<SessionSummary>(`/sessions/${id}`),
    close: (id: string) => req<void>(`/sessions/${id}`, { method: "DELETE" }),
    step: (id: string) => req<SessionSummary>(`/sessions/${id}/step`, { method: "POST" }),
    run: (id: string) => req<SessionSummary>(`/sessions/${id}/run`, { method: "POST" }),
    reset: (id: string, node: string) => req<SessionSummary>(`/sessions/${id}/reset`, json({ node })),
    patch: (id: string) => req<SessionSummary>(`/sessions/${id}/patch`, { method: "POST" }),
    observe: (id: string) => req<{ render: string; state: PageState }>(`/sessions/${id}/observe`, { method: "POST" }),
    state: (id: string) => req<{ render: string; state: PageState } | null>(`/sessions/${id}/state`),
    targets: (id: string) => req<Record<string, TargetReport>>(`/sessions/${id}/targets`),
    recognize: (id: string) => req<Record<string, Recognition>>(`/sessions/${id}/recognize`),
    evaluate: (id: string, condition: Record<string, unknown>) => req<Recognition>(`/sessions/${id}/eval`, json({ condition })),
    screenshot: (id: string) => req<{ jpeg_base64: string }>(`/sessions/${id}/screenshot`),
    events: (id: string, since = 0) => req<BuilderEvent[]>(`/sessions/${id}/events?since=${since}`),
    wsUrl: (id: string) => {
      const proto = location.protocol === "https:" ? "wss" : "ws"
      return `${proto}://${location.host}${BASE}/sessions/${id}/ws`
    },
  },
}
