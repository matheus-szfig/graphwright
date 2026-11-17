import { useEffect, useState } from "react"

import { api, type ApiError, type GraphSummary, type GraphView, type InputSpec, type SchemaBundle, type SessionSummary } from "@/api"
import { useDialog } from "./dialog"
import { Badge, Check, Icon, Section, cls, readFlag, writeFlag } from "./ui"

// `secure` marks a secret input (`password` is its legacy alias).
export const INPUT_TYPES = ["string", "secure", "email", "date", "number", "url", "otp"]
// Fields a credential provider usually carries; any other name works too (free text).
export const CREDENTIAL_FIELDS = ["username", "password", "token", "passphrase", "email"]
const NODE_KINDS = ["step", "code", "outcome", "success", "failure"]

export interface LeftSidebarProps {
  graphs: GraphSummary[]
  schema: SchemaBundle | null
  view: GraphView
  session: SessionSummary | null
  values: Record<string, string>
  onValuesChange: (values: Record<string, string>) => void
  /** Credential reference for the API's provider: fills `credential:` inputs and TOTP requests. */
  credential: string
  onCredentialChange: (ref: string) => void
  onOpenGraph: (name: string) => void
  onGraphChanged: (v: GraphView) => void
  onGraphDeleted: (name: string) => void
  onError: (msg: string, errors?: string[]) => void
  busy: boolean
}

function useCollapsed(key: string) {
  const [collapsed, setCollapsed] = useState(() => readFlag(`builder.section.${key}`))
  useEffect(() => writeFlag(`builder.section.${key}`, collapsed), [key, collapsed])
  return [collapsed, () => setCollapsed((c) => !c)] as const
}

export function LeftSidebar(p: LeftSidebarProps) {
  const [graphCollapsed, toggleGraph] = useCollapsed("graph")
  const [inputsCollapsed, toggleInputs] = useCollapsed("inputs")
  const inputCount = Object.keys(p.view.workflow.inputs).length

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto py-3">
      <Section
        title="Graph"
        icon="account_tree"
        collapsed={graphCollapsed}
        onToggle={toggleGraph}
        summary={`${Object.keys(p.view.workflow.nodes).length} nodes`}
        actions={p.view.lint.length ? <Badge tone="danger" title={p.view.lint.join("\n")} dot>{p.view.lint.length} lint</Badge> : <Badge tone="success" dot>lint ok</Badge>}
      >
        <GraphSection {...p} />
      </Section>

      <Section title="Inputs" icon="data_object" collapsed={inputsCollapsed} onToggle={toggleInputs} summary={inputCount ? `${inputCount} declared` : "none"}>
        <InputsSection {...p} />
      </Section>
    </div>
  )
}

// --- Graph: pick / create graphs, add nodes ------------------------------------------------

function GraphSection(p: LeftSidebarProps) {
  const [creating, setCreating] = useState(false)
  const dialog = useDialog()
  const [ng, setNg] = useState({ name: "", portal: "", url: "" })
  const [node, setNode] = useState({ name: "", kind: "step" })
  const nodeTaken = !!p.view.workflow.nodes[node.name]
  const codeUnavailable = node.kind === "code" && !p.schema?.node_kinds?.includes("code")

  const wrap = async (fn: () => Promise<GraphView>, after?: () => void) => {
    try {
      p.onGraphChanged(await fn())
      after?.()
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }
  const addNode = () => node.name && !nodeTaken && !codeUnavailable && wrap(() => api.graphs.addNode(p.view.name, node.name, node.kind), () => setNode({ ...node, name: "" }))
  const deleteGraph = async () => {
    const name = p.view.name
    const ok = await dialog.confirm({
      title: `Delete ${name}?`,
      message: (
        <>
          <span className="mono">{name}.yaml</span> is removed from the graphs folder
          {p.session?.graph === name ? " and its running session is closed" : ""}. Git keeps the history if it was committed.
        </>
      ),
      tone: "danger",
      icon: "delete",
      confirmLabel: "Delete graph",
    })
    if (!ok) return
    try {
      await api.graphs.delete(name)
      p.onGraphDeleted(name)
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }

  return (
    <>
      <div className="flex items-center gap-1.5">
        <select className={`${cls.select} flex-1`} value={p.view.name} onChange={(e) => p.onOpenGraph(e.target.value)} title="open another graph">
          {p.graphs.map((g) => (
            <option key={g.name} value={g.name}>
              {g.name} · v{g.version} · {g.portal}
              {g.lint_ok ? "" : " ⚠"}
            </option>
          ))}
        </select>
        <button className={cls.btn} onClick={() => setCreating((c) => !c)} title="create a new graph from the template">
          <Icon name={creating ? "close" : "add"} size={16} />
          {creating ? "" : "New"}
        </button>
        <button className={cls.danger} onClick={() => void deleteGraph()} title={`delete ${p.view.name}.yaml`}>
          <Icon name="delete" size={16} />
        </button>
      </div>

      {creating && (
        <div className="space-y-1.5 rounded border border-outline-variant bg-recess/60 p-2">
          <span className={cls.label}>New graph</span>
          <input className={`${cls.input} w-full`} placeholder="name (snake_case) — e.g. acme_record_search" value={ng.name} onChange={(e) => setNg({ ...ng, name: e.target.value.replace(/[^a-z0-9_]/gi, "_").toLowerCase() })} />
          <input className={`${cls.input} w-full`} placeholder="portal — e.g. acme-records" value={ng.portal} onChange={(e) => setNg({ ...ng, portal: e.target.value })} />
          <input className={`${cls.input} w-full`} placeholder="entry url (optional)" value={ng.url} onChange={(e) => setNg({ ...ng, url: e.target.value })} />
          <div className="flex justify-end gap-1.5">
            <button className={cls.btn} onClick={() => setCreating(false)}>
              Cancel
            </button>
            <button
              className={cls.primary}
              disabled={!ng.name || !ng.portal}
              onClick={() =>
                wrap(
                  () => api.graphs.create(ng.name, ng.portal, ng.url || undefined),
                  () => {
                    setCreating(false)
                    setNg({ name: "", portal: "", url: "" })
                  },
                )
              }
            >
              Create
            </button>
          </div>
        </div>
      )}

      <div className="space-y-1">
        <span className={cls.label}>Add node</span>
        <div className="flex items-center gap-1.5" title="type a name, pick a kind, Enter">
          <input
            className={`${cls.input} flex-1 ${nodeTaken ? "border-danger" : ""}`}
            placeholder="node_name"
            value={node.name}
            onChange={(e) => setNode({ ...node, name: e.target.value.replace(/[^a-z0-9_]/gi, "_").toLowerCase() })}
            onKeyDown={(e) => e.key === "Enter" && addNode()}
          />
          <select className={cls.select} value={node.kind} onChange={(e) => setNode({ ...node, kind: e.target.value })}>
            {NODE_KINDS.map((k) => (
              <option key={k}>{k}</option>
            ))}
          </select>
          <button className={cls.primary} disabled={!node.name || nodeTaken || codeUnavailable} title={nodeTaken ? "a node with this name exists" : "add node (Enter)"} onClick={addNode}>
            <Icon name="add" size={16} />
          </button>
        </div>
        {codeUnavailable && (
          <p className="text-xs text-warning">
            {p.schema ? "This API does not support code nodes." : "Code-node support could not be verified."}{" "}
            Stop the existing builder API, run <span className="mono">make builder:local</span> again
            (Docker: <span className="mono">make builder:up</span>), then reload this page.
          </p>
        )}
        <p className={cls.muted}>
          A step or code node is hooked from the start node so it lints; wire it on the canvas by dragging between ports. A code node runs
          sandboxed JavaScript over vars (Code tab).
        </p>
      </div>
    </>
  )
}

// --- Inputs: declare, remove and fill workflow inputs -------------------------------------------

function guessType(name: string, current: string): string {
  if (/password|passwd|pwd|secret|passphrase|token/.test(name)) return "secure"
  if (/otp|totp|2fa|mfa|code$/.test(name)) return "otp"
  if (/e_?mail/.test(name)) return "email"
  if (/url|link|endpoint/.test(name)) return "url"
  if (/date|_at$|_on$/.test(name)) return "date"
  if (/count|amount|number|qty/.test(name)) return "number"
  return current
}

function guessCredential(name: string): string {
  if (/^(user|username|login|account)$/.test(name)) return "username"
  if (/password|passwd|pwd/.test(name)) return "password"
  if (/token/.test(name)) return "token"
  return ""
}

function InputsSection(p: LeftSidebarProps) {
  const [draft, setDraft] = useState({ name: "", type: "string", pii: true })
  const [editing, setEditing] = useState<string | null>(null)
  const specs = Object.entries(p.view.workflow.inputs)
  const taken = !!p.view.workflow.inputs[draft.name]
  const s = p.session
  const locked = !!s

  const put = async (name: string, spec: Partial<InputSpec> & { type: string }, after?: () => void) => {
    try {
      p.onGraphChanged(await api.graphs.putInput(p.view.name, name, spec))
      after?.()
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }
  const add = () => {
    if (!draft.name || taken) return
    const credential = guessCredential(draft.name) || undefined
    void put(draft.name, { type: draft.type, pii: draft.pii, credential }, () => setDraft({ name: "", type: "string", pii: true }))
  }
  const remove = async (name: string) => {
    try {
      p.onGraphChanged(await api.graphs.deleteInput(p.view.name, name))
    } catch (e) {
      p.onError((e as ApiError).message, (e as ApiError).errors)
    }
  }

  const shownValue = (name: string, spec: InputSpec): string => {
    if (!locked) return p.values[name] ?? ""
    const v = s!.inputs[name]
    if (v === undefined) return "—"
    return spec.pii || spec.credential ? "••••••" : String(v)
  }

  return (
    <>
      {specs.length === 0 && <p className={cls.muted}>Nothing declared. Values the graph types (credentials, search terms…) are declared here and referenced in actions as {"{name}"}.</p>}
      {specs.map(([name, spec]) => (
        <div key={name} className="group space-y-1">
          <div className="flex items-center gap-2">
            <span className="mono min-w-0 flex-1 truncate text-[12px] text-on-surface" title={spec.description || name}>{`{${name}}`}</span>
            <span className="mono text-[10px] text-faint">{spec.type === "password" ? "secure" : spec.type}</span>
            {spec.credential && <Badge tone="secondary" title={`filled from the run's credential field "${spec.credential}"`}>cred:{spec.credential}</Badge>}
            {spec.pii && <Badge tone="tertiary" title="personal data / credential — redacted in events, never written to the graph">PII</Badge>}
            {!spec.required && <Badge tone="zinc" title="optional">opt</Badge>}
            {!locked && (
              <>
                <button className="text-faint opacity-0 hover:text-on-surface group-hover:opacity-100" title={`edit input ${name}`} onClick={() => setEditing(editing === name ? null : name)}>
                  <Icon name={editing === name ? "close" : "tune"} size={14} />
                </button>
                <button className="text-faint opacity-0 hover:text-danger-soft group-hover:opacity-100" title={`remove input ${name}`} onClick={() => remove(name)}>
                  <Icon name="delete" size={14} />
                </button>
              </>
            )}
          </div>
          {spec.description && <p className="text-[11px] leading-snug text-faint">{spec.description}</p>}
          {editing === name && !locked ? (
            <InputEditor name={name} spec={spec} onSave={(next) => put(name, next, () => setEditing(null))} onCancel={() => setEditing(null)} />
          ) : (
            <input
              className={`${cls.input} w-full`}
              placeholder={
                locked ? ""
                : spec.credential && p.credential ? `from credential “${p.credential}”`
                : spec.default !== undefined && spec.default !== null ? `default: ${String(spec.default)}`
                : `value for the next session (${spec.type})`
              }
              type={["password", "secure"].includes(spec.type) || spec.pii || !!spec.credential ? "password" : "text"}
              autoComplete="off"
              disabled={locked}
              value={shownValue(name, spec)}
              onChange={(e) => p.onValuesChange({ ...p.values, [name]: e.target.value })}
            />
          )}
        </div>
      ))}
      {!locked && (
        <div className="flex items-center gap-2 border-t border-dashed border-outline-variant pt-3" title="declare a new input: name, type, Enter">
          <input
            className={`${cls.input} flex-1 ${taken ? "border-danger" : ""}`}
            placeholder="name"
            value={draft.name}
            onChange={(e) => {
              const name = e.target.value.replace(/[^a-z0-9_]/gi, "_").toLowerCase()
              setDraft({ ...draft, name, type: guessType(name, draft.type) })
            }}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <select className={`${cls.select} w-[74px] shrink-0 px-1`} value={draft.type} onChange={(e) => setDraft({ ...draft, type: e.target.value })} title="input type">
            {INPUT_TYPES.map((t) => (
              <option key={t}>{t}</option>
            ))}
          </select>
          <Check checked={draft.pii} onChange={(v) => setDraft({ ...draft, pii: v })} label="PII" title="personal data / credential: redacted in events, never written to the graph" />
          <button className={cls.primary} disabled={!draft.name || taken} title={taken ? "an input with this name exists" : "add input (Enter)"} onClick={add}>
            <Icon name="add" size={16} />
          </button>
        </div>
      )}
      <div className="space-y-1 border-t border-dashed border-outline-variant pt-3">
        <span className={cls.label}>Credential reference</span>
        <input
          className={`${cls.input} w-full`}
          placeholder="optional — e.g. demo-user (CREDENTIAL_PROVIDER on the API)"
          value={locked ? (s!.credential ?? "") : p.credential}
          disabled={locked}
          onChange={(e) => p.onCredentialChange(e.target.value.trim())}
        />
        <p className={cls.muted}>Fills <span className="mono">credential:</span> inputs and TOTP requests from the API's provider. Leave empty to type them above.</p>
      </div>
      {locked && <p className={cls.muted}>Inputs are fixed while a session runs.</p>}
    </>
  )
}

function InputEditor({ name, spec, onSave, onCancel }: {
  name: string
  spec: InputSpec
  onSave: (spec: Partial<InputSpec> & { type: string }) => void
  onCancel: () => void
}) {
  const [d, setD] = useState({
    type: spec.type === "password" ? "secure" : spec.type,
    description: spec.description ?? "",
    required: spec.required,
    pii: spec.pii,
    default: spec.default === undefined || spec.default === null ? "" : String(spec.default),
    credential: spec.credential ?? "",
  })
  const save = () =>
    onSave({
      type: d.type,
      description: d.description,
      required: d.required,
      pii: d.pii,
      default: d.credential || d.default === "" ? null : d.default,
      credential: d.credential || null,
    })
  return (
    <div className="space-y-1.5 rounded border border-outline-variant bg-recess/60 p-2" onKeyDown={(e) => e.key === "Escape" && onCancel()}>
      <span className={cls.label}>Edit {`{${name}}`}</span>
      <input className={`${cls.input} w-full`} placeholder="description (shown to whoever starts a run)" value={d.description} onChange={(e) => setD({ ...d, description: e.target.value })} />
      <div className="flex items-center gap-1.5">
        <select className={`${cls.select} flex-1`} value={d.type} onChange={(e) => setD({ ...d, type: e.target.value })} title="input type">
          {uniqueOptions(INPUT_TYPES, d.type).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <input
          className={`${cls.input} flex-1`}
          list="credential-fields"
          placeholder="credential field"
          title="fill from this field of the run's credential (username, password, token…) — empty: the run provides it"
          value={d.credential}
          onChange={(e) => setD({ ...d, credential: e.target.value.replace(/[^a-z0-9_]/gi, "_").toLowerCase() })}
        />
        <datalist id="credential-fields">
          {CREDENTIAL_FIELDS.map((f) => (
            <option key={f} value={f} />
          ))}
        </datalist>
      </div>
      <input
        className={`${cls.input} w-full`}
        placeholder={d.credential ? "credential inputs have no default" : "default (used when a run does not provide it)"}
        disabled={!!d.credential}
        value={d.credential ? "" : d.default}
        onChange={(e) => setD({ ...d, default: e.target.value })}
      />
      <div className="flex items-center gap-3">
        <Check checked={d.required} onChange={(v) => setD({ ...d, required: v })} label="required" />
        <Check checked={d.pii} onChange={(v) => setD({ ...d, pii: v })} label="PII" title="redacted in events" />
        <span className="flex-1" />
        <button className={cls.btn} onClick={onCancel}>
          Cancel
        </button>
        <button className={cls.primary} onClick={save}>
          Save
        </button>
      </div>
    </div>
  )
}

function uniqueOptions(options: string[], current: string): string[] {
  return options.includes(current) ? options : [...options, current]
}
