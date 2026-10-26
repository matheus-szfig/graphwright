// Code — the graph's code nodes and in-page scripts, and a console for the live page.
//
// `script` actions (code nodes) run sandboxed JavaScript: a fresh V8 isolate with no
// network or files, `args` rendered from the action's templates, `vars`/`inputs` as
// non-secret copies. They are tested here without a browser. `evaluate` actions run
// JavaScript in the page (isolated world by default, or the page's own `main` world)
// and need a session. The console at the bottom runs ad-hoc page JavaScript the same
// way. Edits to code here are a scratch copy for testing; the YAML is what is saved.
import { useEffect, useMemo, useState } from "react"

import { api, type ApiError, type ConsoleResult, type EvaluateSpec, type GraphView, type ScriptReport, type ScriptSpec, type SessionSummary } from "@/api"
import { CodeEditor } from "@/editor/CodeEditor"
import { Badge, Icon, cls } from "./ui"

export interface CodePanelProps {
  view: GraphView
  selected?: string
  session: SessionSummary | null
  values: Record<string, string>
  credential: string
  onError: (msg: string, errors?: string[]) => void
}

type CodeAction =
  | { verb: "script"; index: number; scriptIndex: number; spec: ScriptSpec }
  | { verb: "evaluate"; index: number; spec: EvaluateSpec }

function codeActions(view: GraphView, node: string): CodeAction[] {
  const out: CodeAction[] = []
  let scriptIndex = 0
  ;(view.workflow.nodes[node]?.actions ?? []).forEach((a, index) => {
    if (a.script) out.push({ verb: "script", index, scriptIndex: scriptIndex++, spec: a.script as ScriptSpec })
    else if (a.evaluate) out.push({ verb: "evaluate", index, spec: a.evaluate as EvaluateSpec })
  })
  return out
}

function parseObject(text: string): Record<string, unknown> | null {
  if (!text.trim()) return {}
  try {
    const v = JSON.parse(text)
    return v && typeof v === "object" && !Array.isArray(v) ? v : null
  } catch {
    return null
  }
}

const pretty = (v: unknown) => JSON.stringify(v ?? {}, null, 2)

export function CodePanel(p: CodePanelProps) {
  const withCode = useMemo(
    () => Object.keys(p.view.workflow.nodes).filter((n) => codeActions(p.view, n).length > 0),
    [p.view],
  )
  const [node, setNode] = useState<string | undefined>(undefined)
  useEffect(() => {
    if (p.selected && withCode.includes(p.selected)) setNode(p.selected)
    else if (!node || !withCode.includes(node)) setNode(withCode[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.selected, withCode])
  const actions = node ? codeActions(p.view, node) : []

  return (
    <div className="space-y-4 text-xs">
      <section className="space-y-2">
        <div className="flex items-center gap-2">
          <span className={cls.label}>Code in node</span>
          {withCode.length > 0 ? (
            <select className={`${cls.select} flex-1`} value={node} onChange={(e) => setNode(e.target.value)}>
              {withCode.map((n) => (
                <option key={n} value={n}>
                  {n} · {codeActions(p.view, n).map((a) => a.verb).join(", ")}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-muted">none yet</span>
          )}
        </div>
        {withCode.length === 0 && <EmptyHint />}
        {node &&
          actions.map((a) => (
            <CodeActionCard key={`${p.view.name}:${node}:${a.index}`} {...p} node={node} action={a} />
          ))}
      </section>
      <Console {...p} />
    </div>
  )
}

function EmptyHint() {
  return (
    <div className="space-y-2 text-muted">
      <p>
        Add a <span className="mono">code</span> node from the left rail, or put a <span className="mono">script</span> /{" "}
        <span className="mono">evaluate</span> action in any node:
      </p>
      <pre className="mono whitespace-pre rounded border border-outline-variant bg-recess p-2 text-[11px] text-on-surface-variant">{`- script:          # sandboxed JS: vars in → vars out
    into: summary
    args: {events: "{vars.record.events}"}
    code: |
      return {count: args.events.length}
- evaluate:        # JS in the page (isolated world)
    into: rows
    script: return document.querySelectorAll("tr").length`}</pre>
    </div>
  )
}

// --- one script / evaluate action ------------------------------------------------------

function CodeActionCard(p: CodePanelProps & { node: string; action: CodeAction }) {
  const { action } = p
  const saved = action.verb === "script" ? action.spec.code : action.spec.script
  const [code, setCode] = useState(saved)
  const [argsText, setArgsText] = useState(pretty(action.spec.args))
  const modified = code !== saved
  useEffect(() => setCode(saved), [saved])

  return (
    <div className="space-y-2 rounded border border-outline-variant bg-surface-low p-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <Icon name={action.verb === "script" ? "code" : "javascript"} size={16} className="text-primary-soft" />
        <span className="mono text-on-surface">
          #{action.index} {action.verb}
        </span>
        {action.spec.into ? <Badge tone="primary">into {action.spec.into}</Badge> : <Badge tone="zinc">sets returned keys</Badge>}
        {action.verb === "script" && (
          <Badge tone="zinc" title="sandbox limits">
            {action.spec.timeout_ms ?? 1000} ms · {action.spec.memory_mb ?? 64} MB
          </Badge>
        )}
        {action.verb === "evaluate" && (
          <Badge tone={action.spec.world === "main" ? "warning" : "secondary"} title={action.spec.world === "main" ? "runs in the page's own JS context — the page can observe it" : "CDP isolated world: DOM yes, page globals no, invisible to the page"}>
            {action.spec.world ?? "isolated"} world{action.spec.frame ? ` · frame ${String(action.spec.frame)}` : ""}
          </Badge>
        )}
        {action.spec.allow_secrets && <Badge tone="tertiary">allow_secrets</Badge>}
        {modified && (
          <button className={`${cls.ghost} ml-auto`} onClick={() => setCode(saved)} title="discard the scratch edit">
            <Icon name="undo" size={14} /> modified (not saved)
          </button>
        )}
      </div>
      <CodeEditor value={code} onChange={setCode} className="max-h-64 overflow-auto" />
      {action.verb === "script" ? (
        <ScriptTester {...p} code={code} argsText={argsText} setArgsText={setArgsText} spec={action.spec} />
      ) : (
        <EvaluateRunner {...p} code={code} argsText={argsText} setArgsText={setArgsText} spec={action.spec} />
      )}
    </div>
  )
}

function ArgsBox({ text, setText, label = "args (JSON — templates like {input} or {vars.x} are rendered)" }: { text: string; setText: (v: string) => void; label?: string }) {
  const ok = parseObject(text) !== null
  return (
    <label className="block space-y-1">
      <span className={cls.label}>{label}</span>
      <textarea className={`${cls.input} h-16 w-full resize-y ${ok ? "" : "border-danger"}`} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {!ok && <span className="text-danger-soft">must be a JSON object</span>}
    </label>
  )
}

function ScriptTester(
  p: CodePanelProps & { code: string; argsText: string; setArgsText: (v: string) => void; spec: ScriptSpec },
) {
  const declared = Object.entries(p.view.workflow.inputs).filter(([, s]) => !s.credential)
  const [inputs, setInputs] = useState<Record<string, string>>(() => Object.fromEntries(declared.map(([k]) => [k, p.values[k] ?? ""])))
  const [varsText, setVarsText] = useState("{}")
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<ScriptReport | null>(null)
  useEffect(() => {
    setInputs((cur) => Object.fromEntries(declared.map(([k]) => [k, cur[k] || p.values[k] || ""])))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.values])

  const args = parseObject(p.argsText)
  const vars = parseObject(varsText)
  const test = async () => {
    if (args === null || vars === null) return
    setBusy(true)
    try {
      const nonEmpty = Object.fromEntries(Object.entries(inputs).filter(([, v]) => v !== ""))
      setReport(
        await api.graphs.testScript(p.view.name, {
          code: p.code, args, inputs: nonEmpty, vars, into: p.spec.into ?? null, credential: p.credential || undefined,
          timeout_ms: p.spec.timeout_ms, memory_mb: p.spec.memory_mb, allow_secrets: p.spec.allow_secrets,
        }),
      )
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <ArgsBox text={p.argsText} setText={p.setArgsText} />
      {declared.length > 0 && (
        <div className="space-y-1">
          <span className={cls.label}>inputs</span>
          <div className="grid grid-cols-2 gap-1">
            {declared.map(([k, s]) => (
              <label key={k} className="flex items-center gap-1">
                <span className="mono w-24 shrink-0 truncate text-muted" title={s.description}>{k}</span>
                <input
                  className={`${cls.input} min-w-0 flex-1`}
                  type={s.type === "password" ? "password" : "text"}
                  autoComplete="off"
                  placeholder={s.default != null ? `default: ${String(s.default)}` : ""}
                  value={inputs[k] ?? ""}
                  onChange={(e) => setInputs({ ...inputs, [k]: e.target.value })}
                />
              </label>
            ))}
          </div>
        </div>
      )}
      <label className="block space-y-1">
        <span className="flex items-center gap-2">
          <span className={cls.label}>vars (JSON)</span>
          {p.session && (
            <button className={cls.ghost} onClick={() => setVarsText(pretty(p.session?.vars))} title="the running session's vars (secrets show as ***)">
              use session vars
            </button>
          )}
        </span>
        <textarea
          className={`${cls.input} h-16 w-full resize-y ${vars === null ? "border-danger" : ""}`}
          value={varsText}
          onChange={(e) => setVarsText(e.target.value)}
          spellCheck={false}
        />
      </label>
      <button className={cls.primary} disabled={busy || args === null || vars === null} onClick={() => void test()}>
        <Icon name={busy ? "hourglass_top" : "play_arrow"} size={16} />
        {busy ? "Running…" : "Test in sandbox"}
      </button>
      {report && (
        <ResultView
          ok={report.ok} value={report.value} error={report.error} details={report.details} logs={report.logs}
          elapsed={report.elapsed_ms} sets={report.sets}
        />
      )}
    </div>
  )
}

function EvaluateRunner(
  p: CodePanelProps & { code: string; argsText: string; setArgsText: (v: string) => void; spec: EvaluateSpec },
) {
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ConsoleResult | null>(null)
  const args = parseObject(p.argsText)
  const hasTemplates = /\{[a-zA-Z_][\w.[\]]*\}/.test(p.argsText)
  const run = async () => {
    if (!p.session || args === null) return
    setBusy(true)
    try {
      setResult(await api.sessions.console(p.session.id, {
        script: p.code, args, world: p.spec.world ?? "isolated", frame: p.spec.frame ?? 0, timeout_ms: p.spec.timeout_ms,
      }))
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="space-y-2">
      <ArgsBox text={p.argsText} setText={p.setArgsText} label="args (JSON, sent as is)" />
      {hasTemplates && (
        <p className="text-warning">
          Templates are rendered when the graph runs; here the args are sent literally — replace them with sample values to test.
        </p>
      )}
      <button className={cls.primary} disabled={!p.session || busy || args === null} onClick={() => void run()} title={p.session ? "" : "start a session to run JavaScript in the page"}>
        <Icon name={busy ? "hourglass_top" : "play_arrow"} size={16} />
        {busy ? "Running…" : p.session ? "Run in session page" : "Run (needs a session)"}
      </button>
      {result && <ResultView ok={result.ok} value={result.value} error={result.error} elapsed={result.elapsed_ms} />}
    </div>
  )
}

export function ResultView(r: {
  ok: boolean; value: unknown; error: string | null; details?: string[]; logs?: string[]; elapsed: number | null; sets?: string[]
}) {
  return (
    <div className={`space-y-1.5 rounded border p-2 ${r.ok ? "border-success/40 bg-success/5" : "border-danger/40 bg-danger/5"}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone={r.ok ? "success" : "danger"} dot>{r.ok ? "ok" : "error"}</Badge>
        {r.elapsed != null && <Badge tone="zinc">{r.elapsed} ms</Badge>}
        {r.ok && r.sets && r.sets.length > 0 && (
          <span className="text-muted">
            sets <span className="mono text-on-surface">{r.sets.join(", ")}</span>
          </span>
        )}
      </div>
      {r.ok ? (
        <pre className="mono max-h-56 overflow-auto whitespace-pre-wrap text-on-surface">{JSON.stringify(r.value, null, 2)}</pre>
      ) : (
        <pre className="mono max-h-56 overflow-auto whitespace-pre-wrap text-danger-soft">
          {[r.error, ...(r.details ?? [])].filter(Boolean).join("\n")}
        </pre>
      )}
      {r.logs && r.logs.length > 0 && (
        <div>
          <span className={cls.label}>console</span>
          <pre className="mono max-h-40 overflow-auto whitespace-pre-wrap text-muted">{r.logs.join("\n")}</pre>
        </div>
      )}
    </div>
  )
}

// --- console ---------------------------------------------------------------------------

interface ConsoleEntry { id: number; script: string; world: string; frame: number; result: ConsoleResult }

function Console(p: CodePanelProps) {
  const [code, setCode] = useState("return document.title")
  const [world, setWorld] = useState<"isolated" | "main">("isolated")
  const [frame, setFrame] = useState(0)
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<ConsoleEntry[]>([])
  const sid = p.session?.id

  const run = async () => {
    if (!sid || busy) return
    setBusy(true)
    try {
      const result = await api.sessions.console(sid, { script: code, world, frame })
      setHistory((h) => [{ id: Date.now(), script: code, world, frame, result }, ...h].slice(0, 20))
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="space-y-2 border-t border-outline-variant pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <Icon name="terminal" size={16} className="text-secondary-soft" />
        <span className={cls.label}>Page console</span>
        <span className="text-faint">async function body · return a JSON value · Ctrl+Enter runs</span>
      </div>
      <CodeEditor value={code} onChange={setCode} onSubmit={() => void run()} className="max-h-48 overflow-auto" />
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex overflow-hidden rounded border border-outline-variant">
          {(["isolated", "main"] as const).map((w) => (
            <button
              key={w}
              onClick={() => setWorld(w)}
              className={`px-2 py-1 ${world === w ? "bg-primary/20 text-primary-soft" : "text-muted hover:bg-surface-high"}`}
              title={w === "main" ? "the page's own JS context: page globals, observable by the page" : "CDP isolated world: DOM access, invisible to the page"}
            >
              {w}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1 text-muted">
          frame
          <input className={`${cls.input} w-14`} type="number" min={0} value={frame} onChange={(e) => setFrame(Math.max(0, Number(e.target.value) || 0))} />
        </label>
        <button className={cls.primary} disabled={!sid || busy} onClick={() => void run()} title={sid ? "" : "start a session first"}>
          <Icon name={busy ? "hourglass_top" : "play_arrow"} size={16} />
          {busy ? "Running…" : "Run"}
        </button>
        {history.length > 0 && (
          <button className={cls.ghost} onClick={() => setHistory([])}>
            clear
          </button>
        )}
      </div>
      {!sid && <p className="text-muted">Start a session to run JavaScript in its page.</p>}
      <div className="space-y-1.5">
        {history.map((h) => (
          <div key={h.id} className="space-y-1">
            <div className="mono flex items-center gap-1.5 text-faint">
              <Badge tone={h.world === "main" ? "warning" : "secondary"}>{h.world}</Badge>
              {h.frame > 0 && <Badge tone="zinc">frame {h.frame}</Badge>}
              <button className="truncate text-left hover:text-on-surface" title="load into the editor" onClick={() => setCode(h.script)}>
                {h.script.split("\n")[0].slice(0, 90)}
              </button>
            </div>
            <ResultView ok={h.result.ok} value={h.result.value} error={h.result.error} elapsed={h.result.elapsed_ms} />
          </div>
        ))}
      </div>
    </section>
  )
}
