// Graph requests — declared under `requests:` in the graph, fetched at the moment an
// action uses them. This tab lists them and runs one on demand: inside the live session
// (its inputs, vars and credential apply, and the result feeds `request:` conditions) or
// standalone with the values typed here. Every attempt is shown, secrets masked unless
// "reveal" is on (local debugging only — the API never logs revealed values).
import { useEffect, useMemo, useState } from "react"

import { api, type ApiError, type GraphView, type RequestAttempt, type RequestCall, type RequestReport, type RequestSpec, type SessionSummary } from "@/api"
import { Badge, Check, Icon, cls, type Tone } from "./ui"

export interface RequestsPanelProps {
  view: GraphView
  session: SessionSummary | null
  values: Record<string, string>
  credential: string
  onError: (msg: string, errors?: string[]) => void
}

const OUTCOME_TONE: Record<string, Tone> = { ok: "success", retry: "warning", failed: "danger" }
const STATUS_TONE: Record<string, Tone> = { ok: "success", failed: "danger", pending: "zinc" }

/** Inputs a request's templates read: `{name}` roots that are declared inputs. */
function inputsUsed(spec: RequestSpec, inputs: string[]): string[] {
  const text = JSON.stringify(spec)
  const used = new Set<string>()
  for (const m of text.matchAll(/\{([a-zA-Z_]\w*)/g)) if (inputs.includes(m[1])) used.add(m[1])
  return [...used]
}

function describe(spec: RequestSpec): string {
  if (spec.kind === "http") return `${spec.method ?? "GET"} ${spec.url ?? ""}`
  if (spec.kind === "totp") return spec.secret ? `TOTP from ${spec.secret}` : "TOTP from the run's credential"
  return `IMAP ${spec.imap?.host ?? ""}${spec.imap?.from ? ` from ${spec.imap.from}` : ""}`
}

function retryLabel(spec: RequestSpec): string {
  const r = spec.retry ?? {}
  const attempts = r.attempts ?? 1
  if (attempts <= 1) return "no retry"
  const parts = [`${attempts} attempts`, `backoff ${(r.backoff_s ?? [1]).join("/")}s`]
  if (r.until_extracted) parts.push("polls until extracted")
  return parts.join(" · ")
}

export function RequestsPanel(p: RequestsPanelProps) {
  const requests = Object.entries(p.view.workflow.requests ?? {})
  const [selected, setSelected] = useState<string | null>(requests[0]?.[0] ?? null)
  useEffect(() => {
    if (!selected || !p.view.workflow.requests?.[selected]) setSelected(requests[0]?.[0] ?? null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.view])

  if (!requests.length) {
    return (
      <div className="space-y-2 text-sm text-muted">
        <p>This graph declares no requests.</p>
        <p className="text-xs">
          Requests fetch short-lived values (2FA codes, one-time links) at the moment an action needs them. Declare one under{" "}
          <span className="mono">requests:</span> in the Graph YAML tab, then use it as <span className="mono">{"{requests.<name>.<field>}"}</span> in a fill, or
          with <span className="mono">fetch: {"{request: <name>, on_error: continue}"}</span> and route on{" "}
          <span className="mono">{"request: {name: <name>, status: failed}"}</span>.
        </p>
        <pre className="mono whitespace-pre rounded border border-outline-variant bg-recess p-2 text-[11px] text-on-surface-variant">{`requests:
  otp:
    url: "{otp_api}/codes/latest"
    headers: {Authorization: "Bearer {otp_token}"}
    extract:
      code: {json: data.code}
    retry: {attempts: 5, backoff_s: [2], until_extracted: true}`}</pre>
      </div>
    )
  }

  return (
    <div className="space-y-3 text-xs">
      <div className="space-y-1">
        {requests.map(([name, spec]) => {
          const status = p.session?.requests?.[name]
          return (
            <button
              key={name}
              onClick={() => setSelected(name)}
              className={`flex w-full items-center gap-2 rounded border px-2 py-1.5 text-left ${selected === name ? "border-primary/60 bg-primary/10" : "border-outline-variant bg-recess hover:bg-surface-high"}`}
            >
              <span className="mono font-semibold text-on-surface">{name}</span>
              <Badge tone="secondary">{spec.kind}</Badge>
              {spec.sensitive !== false && <Badge tone="tertiary" title="extracted values are Secrets: typed, never shown">secret</Badge>}
              {spec.cache === "run" && <Badge tone="zinc" title="the first good result is reused for the run">cached</Badge>}
              <span className="mono min-w-0 flex-1 truncate text-muted" title={describe(spec)}>{spec.description || describe(spec)}</span>
              {status && <Badge tone={STATUS_TONE[status]} dot>{status}</Badge>}
            </button>
          )
        })}
      </div>
      {selected && p.view.workflow.requests?.[selected] && (
        <RequestTester key={`${p.view.name}:${selected}`} name={selected} spec={p.view.workflow.requests[selected]} {...p} />
      )}
    </div>
  )
}

function RequestTester(p: RequestsPanelProps & { name: string; spec: RequestSpec }) {
  const declared = Object.keys(p.view.workflow.inputs)
  const used = useMemo(() => inputsUsed(p.spec, declared), [p.spec, declared])
  const [inputs, setInputs] = useState<Record<string, string>>(() => Object.fromEntries(used.map((k) => [k, p.values[k] ?? ""])))
  const [varsText, setVarsText] = useState("")
  const [reveal, setReveal] = useState(false)
  const [standalone, setStandalone] = useState(false)
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<RequestReport | null>(null)
  const inSession = !!p.session && !standalone

  // Keep the form in step with the Inputs panel until the author edits it here.
  useEffect(() => {
    setInputs((cur) => Object.fromEntries(used.map((k) => [k, cur[k] || p.values[k] || ""])))
  }, [p.values, used])

  const parseVars = (): Record<string, unknown> | null => {
    if (!varsText.trim()) return {}
    try {
      const v = JSON.parse(varsText)
      return v && typeof v === "object" && !Array.isArray(v) ? v : null
    } catch {
      return null
    }
  }
  const vars = parseVars()

  const test = async () => {
    if (vars === null) return
    setBusy(true)
    try {
      const r = inSession
        ? await api.sessions.request(p.session!.id, p.name, { vars, reveal })
        : await api.graphs.testRequest(p.view.name, p.name, { inputs, vars, reveal, credential: p.credential || undefined })
      setReport(r)
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    } finally {
      setBusy(false)
    }
  }

  const spec = p.spec
  return (
    <div className="space-y-3 rounded border border-outline-variant bg-surface-low p-2">
      <div className="space-y-1">
        <div className="mono break-all text-on-surface">{describe(spec)}</div>
        {spec.description && <div className="text-muted">{spec.description}</div>}
        <div className="flex flex-wrap gap-1.5">
          <Badge tone="zinc">{retryLabel(spec)}</Badge>
          {spec.kind === "http" && <Badge tone="zinc">expects {(spec.expect_status ?? ["2xx"]).join(", ")}</Badge>}
          {spec.kind === "http" && spec.retry?.on_status && <Badge tone="zinc">retries on {spec.retry.on_status.join(", ")}</Badge>}
          {Object.entries(spec.extract ?? {}).map(([k, ex]) => (
            <Badge key={k} tone="primary" title={ex.json ? `json ${ex.json}` : ex.regex ? `regex ${ex.regex}` : `header ${ex.header}`}>
              {k}{ex.required === false ? "?" : ""}
            </Badge>
          ))}
          {spec.kind === "totp" && <Badge tone="primary">code</Badge>}
        </div>
      </div>

      {!inSession && used.length > 0 && (
        <div className="space-y-1">
          <span className={cls.label}>Inputs</span>
          {used.map((k) => {
            const ispec = p.view.workflow.inputs[k]
            return (
              <label key={k} className="flex items-center gap-2">
                <span className="mono w-28 shrink-0 truncate text-muted" title={ispec?.description}>{`{${k}}`}</span>
                <input
                  className={`${cls.input} flex-1`}
                  type={ispec?.type === "password" || ispec?.credential ? "password" : "text"}
                  autoComplete="off"
                  placeholder={ispec?.credential && p.credential ? `from credential “${p.credential}”` : ispec?.default != null ? `default: ${String(ispec.default)}` : ""}
                  value={inputs[k] ?? ""}
                  onChange={(e) => setInputs({ ...inputs, [k]: e.target.value })}
                />
              </label>
            )
          })}
        </div>
      )}

      <label className="block space-y-1">
        <span className={cls.label}>vars (JSON, optional)</span>
        <textarea
          className={`${cls.input} h-14 w-full resize-y ${vars === null ? "border-danger" : ""}`}
          placeholder='{"record": {"id": "48213"}}'
          value={varsText}
          onChange={(e) => setVarsText(e.target.value)}
        />
        {vars === null && <span className="text-danger-soft">vars must be a JSON object</span>}
      </label>

      <div className="flex flex-wrap items-center gap-3">
        <button className={cls.primary} disabled={busy || vars === null} onClick={() => void test()}>
          <Icon name={busy ? "hourglass_top" : "play_arrow"} size={16} />
          {busy ? "Running…" : inSession ? "Test in session" : "Test"}
        </button>
        {p.session && <Check checked={standalone} onChange={setStandalone} label="standalone" title="ignore the running session and use the inputs typed here" />}
        <Check checked={reveal} onChange={setReveal} label="reveal" title="show extracted secret values in this panel (local debugging only)" />
        {inSession && <span className="text-faint">uses session {p.session!.id}'s inputs, vars and credential</span>}
      </div>

      {report && <RequestReportView report={report} />}
    </div>
  )
}

export function RequestReportView({ report }: { report: RequestReport }) {
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Badge tone={report.ok ? "success" : "danger"} dot>{report.ok ? "ok" : "failed"}</Badge>
        {report.error && <span className="text-danger-soft">{report.error}</span>}
      </div>
      {report.values && Object.keys(report.values).length > 0 && <Values values={report.values} />}
      {report.calls.map((c, i) => (
        <RequestCallView key={i} call={c} />
      ))}
    </div>
  )
}

function Values({ values }: { values: Record<string, unknown> }) {
  return (
    <div className="mono grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 rounded border border-outline-variant bg-recess p-2 text-[11px]">
      {Object.entries(values).map(([k, v]) => (
        <span key={k} className="contents">
          <span className="text-muted">{k}</span>
          <span className="break-all text-on-surface">{typeof v === "string" ? v : JSON.stringify(v)}</span>
        </span>
      ))}
    </div>
  )
}

/** One call of a request: header line + attempt timeline. Used by the Requests and Events tabs. */
export function RequestCallView({ call, compact = false }: { call: RequestCall; compact?: boolean }) {
  return (
    <div className="space-y-1.5">
      {!compact && (
        <div className="flex items-center gap-2 text-[11px]">
          <span className="mono font-semibold text-on-surface">{call.name}</span>
          <Badge tone="secondary">{call.request_kind}</Badge>
          <Badge tone={call.ok ? "success" : "danger"}>{call.ok ? "ok" : call.code ?? "failed"}</Badge>
          <span className="text-faint">{call.attempts.length} attempt{call.attempts.length === 1 ? "" : "s"}</span>
        </div>
      )}
      {!call.attempts.length && call.error && <div className="text-danger-soft">{call.error}</div>}
      <ol className="space-y-1 border-l border-outline-variant pl-3">
        {call.attempts.map((a) => (
          <AttemptView key={a.n} a={a} />
        ))}
      </ol>
    </div>
  )
}

function requestLine(r: Record<string, unknown> | undefined): string {
  if (!r) return ""
  if (r.url) {
    const q = r.query && typeof r.query === "object" ? `  ?${Object.entries(r.query as Record<string, string>).map(([k, v]) => `${k}=${v}`).join("&")}` : ""
    return `${r.method ?? "GET"} ${r.url}${q}`
  }
  return Object.entries(r).filter(([, v]) => v != null).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join("  ")
}

function AttemptView({ a }: { a: RequestAttempt }) {
  const headers = a.request?.headers as Record<string, string> | undefined
  const body = a.request?.body as string | undefined
  return (
    <li className="relative space-y-1">
      <span className={`absolute -left-[17px] top-1 h-2 w-2 rounded-full ${a.outcome === "ok" ? "bg-success" : a.outcome === "retry" ? "bg-warning" : "bg-danger"}`} />
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <span className="mono text-faint">#{a.n}</span>
        <Badge tone={OUTCOME_TONE[a.outcome] ?? "zinc"}>{a.outcome}</Badge>
        {a.status !== undefined && <span className={`mono ${a.status < 400 ? "text-success" : "text-danger-soft"}`}>{a.status}</span>}
        <span className="mono text-muted">{a.elapsed_ms} ms</span>
        {a.wait_s !== undefined && <span className="text-faint">then waited {a.wait_s}s</span>}
        {a.missing?.length ? <span className="text-warning">missing {a.missing.join(", ")}</span> : null}
      </div>
      {a.error && <div className="mono text-[11px] text-danger-soft">{a.error}</div>}
      <div className="mono break-all text-[11px] text-on-surface-variant">{requestLine(a.request)}</div>
      {a.extracted && Object.keys(a.extracted).length > 0 && <Values values={a.extracted} />}
      {(headers || body || a.body_excerpt || a.response_headers) && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-faint hover:text-on-surface">request / response</summary>
          <div className="mt-1 space-y-1">
            {headers && <pre className="mono whitespace-pre-wrap rounded bg-recess p-1.5 text-muted">{Object.entries(headers).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre>}
            {body && <pre className="mono whitespace-pre-wrap rounded bg-recess p-1.5 text-muted">{body}</pre>}
            {a.response_headers && <pre className="mono whitespace-pre-wrap rounded bg-recess p-1.5 text-faint">{Object.entries(a.response_headers).map(([k, v]) => `${k}: ${v}`).join("\n")}</pre>}
            {a.body_excerpt && <pre className="mono max-h-48 overflow-auto whitespace-pre-wrap rounded bg-recess p-1.5 text-on-surface-variant">{a.body_excerpt}</pre>}
          </div>
        </details>
      )}
    </li>
  )
}
