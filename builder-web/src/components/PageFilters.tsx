import { useEffect, useState } from "react"
import { api, ApiError, type ElementCategory, type PageElement, type PageFilter } from "@/api"
import { useDraft } from "@/workspace"
import type { InspectorProps } from "./Inspector"
import { cls } from "./ui"

const categories: ElementCategory[] = ["controls", "links", "media", "status", "text", "tables", "structure", "other"]
const defaults: PageFilter = {
  categories: ["controls", "links", "media", "status"], roles: [], tags: [], states: [],
  query: "", frame: null, visibility: "visible", viewport: "all",
}
const split = (text: string) => text.split(",").map(s => s.trim()).filter(Boolean)

function ListFilter({ field, values, onChange }: { field: string; values: string[]; onChange: (values: string[]) => void }) {
  const [text, setText] = useState(values.join(", "))
  const serialized = JSON.stringify(values)
  useEffect(() => setText(old => JSON.stringify(split(old)) === serialized ? old : (JSON.parse(serialized) as string[]).join(", ")), [serialized])
  return <label className="space-y-1 text-muted">
    <span>{field} (comma separated)</span>
    <input aria-label={`Filter ${field}`} className={`${cls.input} w-full`} value={text} onChange={e => { setText(e.target.value); onChange(split(e.target.value)) }} />
  </label>
}

// Old observations/goldens have no category yet.
function categoryOf(e: PageElement): ElementCategory {
  if (e.category && e.category !== "other") return e.category
  if (e.role === "link") return "links"
  if (e.role === "img") return "media"
  if (["alert", "status", "progressbar", "meter"].includes(e.role)) return "status"
  if (["heading", "paragraph"].includes(e.role)) return "text"
  if (["button", "textbox", "combobox", "checkbox", "radio", "tab", "menuitem", "option", "switch", "slider", "spinbutton", "fileinput", "clickable"].includes(e.role)) return "controls"
  return "other"
}

export function matchesPageFilter(e: PageElement, f: PageFilter): boolean {
  const visible = e.visible !== false
  if (f.visibility !== "all" && visible !== (f.visibility === "visible")) return false
  if (f.viewport !== "all" && e.in_viewport !== (f.viewport === "inside")) return false
  if (f.frame !== null && e.frame !== f.frame) return false
  if (f.categories.length && !f.categories.includes(categoryOf(e))) return false
  if (f.roles.length && !f.roles.includes(e.role)) return false
  if (f.tags.length && !f.tags.includes(e.tag)) return false
  if (f.states.length && !f.states.some(state => e.states.includes(state))) return false
  const text = [e.id, e.dom_id, e.name, e.value, e.role, e.tag, e.href, e.landmark, ...e.nearby, ...e.options, ...e.states].join(" ").toLowerCase()
  return text.includes(f.query.trim().toLowerCase())
}

export function usePageFilters(p: InspectorProps) {
  const saved = JSON.stringify({ ...defaults, ...p.view.workflow.page_filter })
  const draft = useDraft(p.view.name, "page", "filters", saved)
  const [saving, setSaving] = useState(false)
  useEffect(() => draft.receive(saved), [saved, draft.receive])
  const filter: PageFilter = JSON.parse(draft.text)
  const update = (change: Partial<PageFilter>) => draft.edit(JSON.stringify({ ...filter, ...change }))
  const inventory = [...(p.page?.state.elements ?? []), ...(p.page?.state.hidden_elements ?? []).map(e => ({ ...e, visible: false }))]
  const elements = inventory.filter(e => matchesPageFilter(e, filter))
  const save = async () => {
    const submitted = draft.text
    setSaving(true)
    try {
      const view = await api.graphs.putPageFilter(p.view.name, JSON.parse(submitted), JSON.parse(draft.base ?? saved))
      draft.commit(submitted, JSON.stringify({ ...defaults, ...view.workflow.page_filter }))
      p.onGraphChanged(view)
    } catch (err) { p.onError((err as ApiError).message, (err as ApiError).errors) }
    finally { setSaving(false) }
  }
  const controls = <div className="space-y-2 rounded border border-outline-variant bg-recess p-2">
    <div className="flex flex-wrap gap-2">
      <input aria-label="Search page elements" className={`${cls.input} w-full`} placeholder="Search names, IDs, text, URLs…" value={filter.query} onChange={e => update({ query: e.target.value })} />
      <select aria-label="Element visibility" className={cls.select} value={filter.visibility} onChange={e => update({ visibility: e.target.value as PageFilter["visibility"] })}>
        <option value="visible">Visible elements</option><option value="hidden">Hidden elements</option><option value="all">Visible + hidden</option>
      </select>
      <select aria-label="Element viewport" className={cls.select} value={filter.viewport} onChange={e => update({ viewport: e.target.value as PageFilter["viewport"] })}>
        <option value="all">Any position</option><option value="inside">In viewport</option><option value="outside">Outside viewport</option>
      </select>
      <select aria-label="Element frame" className={cls.select} value={filter.frame ?? ""} onChange={e => update({ frame: e.target.value === "" ? null : Number(e.target.value) })}>
        <option value="">All frames</option>
        {filter.frame !== null && !p.page?.state.frames.some(f => f.index === filter.frame) && <option value={filter.frame}>Frame {filter.frame} (not observed)</option>}
        {p.page?.state.frames.map(f => <option key={f.index} value={f.index}>Frame {f.index}: {f.route}</option>)}
      </select>
    </div>
    <div className="flex flex-wrap gap-1" aria-label="Element categories">
      <button className={cls.btn} onClick={() => update({ categories: [] })} aria-pressed={!filter.categories.length}>All types</button>
      {categories.map(category => <button key={category} aria-pressed={!filter.categories.length || filter.categories.includes(category)}
        className={`${cls.btn} ${!filter.categories.length || filter.categories.includes(category) ? "border-primary text-primary-soft" : ""}`}
        onClick={() => {
          // Clicking a category while all are selected focuses that category.
          const next = !filter.categories.length ? [category] : filter.categories.includes(category)
            ? filter.categories.filter(c => c !== category) : [...filter.categories, category]
          update({ categories: next })
        }}>
        {category} <span className="text-faint">{inventory.filter(e => categoryOf(e) === category).length}</span>
      </button>)}
    </div>
    <details>
      <summary className="cursor-pointer text-muted">More filters: role, tag, state</summary>
      <div className="mt-2 grid gap-2 sm:grid-cols-3">
        {(["roles", "tags", "states"] as const).map(field => <ListFilter key={field} field={field} values={filter[field]} onChange={values => update({ [field]: values })} />)}
      </div>
    </details>
    <div className="flex flex-wrap items-center gap-2">
      <span className="mr-auto text-muted">{elements.length} shown / {inventory.length} observed · {inventory.length - elements.length} filtered out</span>
      <button className={cls.btn} onClick={() => draft.edit(JSON.stringify({ ...defaults, categories: [], visibility: "all" }))}>Show everything</button>
      <button className={cls.btn} onClick={() => draft.edit(JSON.stringify(defaults))}>Essentials</button>
      <button className={cls.btn} disabled={saving} onClick={draft.discard}>Load graph defaults</button>
      <button className={cls.primary} disabled={saving || !draft.dirty || draft.conflict} onClick={save}>{saving ? "Saving…" : "Save filters to graph"}</button>
    </div>
    {draft.conflict && <p className="text-warning">Graph defaults changed. Load graph defaults before saving.</p>}
    <p className="text-faint">Filters change this view only. Targets and recognition use the full visible observation. Hidden rows are inspection only; their values are omitted.</p>
  </div>
  return { elements, controls }
}
