import { useEffect, useState } from "react"
import { api, type TargetEntry } from "@/api"
import { useDraft } from "@/workspace"
import { YamlEditor } from "@/editor/YamlEditor"
import type { EditorContext } from "@/editor/completions"
import type { InspectorProps } from "./Inspector"
import { useDialog } from "./dialog"
import { Badge, cls } from "./ui"

export function TargetsPanel(p: InspectorProps) {
  const dialog = useDialog()
  const sid = p.session?.status === "stopped" ? undefined : p.sessionId
  const [snapshot, setSnapshot] = useState<{ graph: string; yaml: string; sid?: string; entries: Record<string, TargetEntry> } | null>(null)
  const [error, setError] = useState("")
  const [saving, setSaving] = useState(false)
  const [query, setQuery] = useState("")
  useEffect(() => {
    let cancelled = false
    api.graphs.targets(p.view.name, sid).then((entries) => {
      if (!cancelled) { setSnapshot({ graph: p.view.name, yaml: p.view.yaml, sid, entries }); setError("") }
    }).catch((e) => { if (!cancelled) { setError(String(e)); setSnapshot(null) } })
    return () => { cancelled = true }
  }, [p.view.name, p.view.yaml, sid, p.events.length, p.page])
  const entries = snapshot?.graph === p.view.name ? snapshot.entries : {}
  const live = !!sid && snapshot?.sid === sid && snapshot.yaml === p.view.yaml
  const add = async () => {
    const name = await dialog.prompt({ title: "Add target", label: "Target name", placeholder: "codigo_2fa",
      validate: (v) => !/^[a-z][a-z0-9_]*$/.test(v) ? "Use snake_case" : entries[v] ? "Target already exists" : null })
    if (!name) return
    setSaving(true)
    try {
      p.onGraphChanged(await api.graphs.putTarget(p.view.name, name, 'description: ""\nstrategies: []\n', ""))
      p.onNotice(`Added ${name}. Expand it to configure its locator strategies.`)
    } catch (e) { p.onError(String(e)) } finally { setSaving(false) }
  }
  return <div className="space-y-3 text-xs">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-muted">{Object.keys(entries).length} targets{live ? ` · ${Object.values(entries).filter(e => e.report?.ok).length} active on the observed page` : " · no live observation"}</span>
      <div className="flex gap-2">
        {sid && <button className={cls.btn} disabled={p.busy} onClick={p.onObserve}>Re-detect</button>}
        <button className={cls.primary} disabled={saving} onClick={add}>Add target</button>
      </div>
    </div>
    <input className={`${cls.input} w-full`} aria-label="Filter targets" placeholder="Filter by name or description" value={query} onChange={e => setQuery(e.target.value)} />
    {error && <p role="alert" className="text-danger-soft">{error}</p>}
    {!snapshot && !error && <p className="text-muted">Loading targets…</p>}
    {snapshot && !Object.keys(entries).length && <p className="text-muted">No targets yet. Add one here or select an element in the Page tab.</p>}
    <p className="text-muted">Strategies are alternatives, tried in order. Edit description, strategies, frame and uniqueness below.{sid ? " Save changes, then Patch before executing them in this session." : " You can edit without starting a browser."}</p>
    {Object.entries(entries).filter(([name, e]) => `${name} ${e.description}`.toLowerCase().includes(query.toLowerCase())).map(([name, entry]) =>
      <TargetCard key={`${p.view.name}:${name}`} name={name} entry={entry} live={live} {...p} />)}
  </div>
}

function TargetCard(p: InspectorProps & { name: string; entry: TargetEntry; live: boolean }) {
  const dialog = useDialog()
  const draft = useDraft(p.view.name, "targets", p.name, p.entry.yaml)
  useEffect(() => { draft.receive(p.entry.yaml) }, [p.entry.yaml, draft.receive])
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState("")
  const report = p.live ? p.entry.report : undefined
  const active = report?.ok === true
  const context: EditorContext = { root: "target", schema: p.schema, graph: p.completions, page: p.page?.state ?? null }
  const save = async () => {
    setSaving(true); setError("")
    const submitted = draft.text
    try {
      const view = await api.graphs.putTarget(p.view.name, p.name, submitted, draft.base ?? p.entry.yaml)
      draft.commit(submitted)
      p.onGraphChanged(view)
      p.onNotice(`Saved ${p.name}.${p.sessionId ? " Patch to apply it to the running session." : ""}`)
    } catch (e) { setError(String(e)) } finally { setSaving(false) }
  }
  const remove = async () => {
    if (!await dialog.confirm({ title: `Delete ${p.name}?`, tone: "danger", confirmLabel: "Delete target",
      message: p.entry.references.length ? `Used in ${p.entry.references.length} places. These references will remain and must be repaired before running.${draft.dirty ? " Unsaved edits will be discarded." : ""}` : `Remove this target?${draft.dirty ? " Unsaved edits will be discarded." : ""}` })) return
    setSaving(true); setError("")
    try {
      const view = await api.graphs.deleteTarget(p.view.name, p.name)
      draft.discard(); p.onGraphChanged(view)
    } catch (e) { setError(String(e)) } finally { setSaving(false) }
  }
  return <details data-target={p.name} className={`rounded border p-3 ${active ? "border-success/50 bg-success/10" : "border-outline-variant bg-recess"}`}>
    <summary className="flex cursor-pointer flex-wrap items-center gap-2">
      <span className={`inline-block h-2 w-2 rounded-full ${active ? "bg-success" : "bg-surface-high"}`} />
      <span className={`mono font-semibold ${active ? "text-success" : ""}`}>{p.name}</span>
      <Badge tone={active ? "success" : "zinc"}>{active ? "Active" : report ? "Not matched" : "Not observed"}</Badge>
      {draft.dirty && <Badge tone="warning">WIP</Badge>}
      <span className="text-muted">{p.entry.description}</span>
    </summary>
    <div className="mt-3 space-y-3">
      <YamlEditor memoryKey={draft.key} value={draft.text} onChange={draft.edit} context={context} className="h-64 overflow-hidden rounded border border-outline-variant" />
      {draft.conflict && <p className="text-warning">This target changed elsewhere. Discard your draft to load the saved version before editing again.</p>}
      {error && <p role="alert" className="text-danger-soft">{error}</p>}
      <div className="flex flex-wrap gap-2">
        <button className={cls.primary} disabled={saving || !draft.dirty || draft.conflict} onClick={save}>Save target</button>
        <button className={cls.btn} disabled={saving || !draft.dirty} onClick={() => { draft.discard(); setError("") }}>Discard edits</button>
        <button className={`${cls.danger} ml-auto`} disabled={saving} onClick={remove}>Delete target</button>
      </div>
      <details><summary className="cursor-pointer text-muted">Used in {p.entry.references.length} places</summary>
        <ul className="mono mt-1 space-y-1 break-all text-muted">{p.entry.references.map(ref => <li key={ref}>{ref}</li>)}</ul>
      </details>
      {report && <div className="space-y-1 text-muted">
        {report.element && <p className="text-success">Matched {report.element.id}: {report.element.role} {report.element.name}</p>}
        <ul className="mono break-all">{report.attempts.map((attempt, i) => <li key={i}>{attempt}</li>)}</ul>
      </div>}
    </div>
  </details>
}
