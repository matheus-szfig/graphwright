// Autocomplete for the graph YAML. Three sources, picked by cursor position:
//   schema — keys and enum values valid at this path (from GET /schema, so it tracks the engine)
//   graph  — node names, target names, inputs, requests, shapers (GET /graphs/{name}/completions)
//   page   — what the live observation shows: dom_ids, roles, names, nearby text, route
import type { Completion, CompletionContext, CompletionResult } from "@codemirror/autocomplete"

import type { PageState } from "@/api"
import { yamlContextAt } from "./yaml-path"

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type JSONSchema = any

export interface SchemaBundle {
  workflow: JSONSchema
  node: JSONSchema
  shapers: string[]
  verbs: string[]
  condition_kinds: string[]
  strategy_fields: string[]
  roles: string[]
  request_kinds?: string[]
  request_fields?: string[]
}

export interface GraphCompletions {
  nodes: string[]; targets: string[]; inputs: string[]; shapers: string[]; macros: string[]
  requests?: Record<string, string[]> // request name → fields readable as {requests.<name>.<field>}
}

// Keys whose value is a template ({input}, {vars.x}, {requests.r.field}).
const TEMPLATE_KEYS = ["value", "url", "option", "secret", "body", "username", "password", "host", "entry_url"]

export interface EditorContext {
  root: "workflow" | "node"
  schema: SchemaBundle | null
  graph: GraphCompletions | null
  page: PageState | null
}

// --- JSON-schema walking --------------------------------------------------------------

function deref(s: JSONSchema, root: JSONSchema): JSONSchema {
  let guard = 0
  while (s && s.$ref && guard++ < 20) {
    const name = String(s.$ref).replace("#/$defs/", "")
    s = root.$defs?.[name]
  }
  return s
}

function branches(s: JSONSchema, root: JSONSchema): JSONSchema[] {
  s = deref(s, root)
  if (!s) return []
  const alts = s.anyOf ?? s.oneOf ?? s.allOf
  if (Array.isArray(alts)) return alts.flatMap((a: JSONSchema) => branches(a, root))
  return [s]
}

function schemaAt(root: JSONSchema, path: string[]): JSONSchema[] {
  let cur = branches(root, root)
  for (const seg of path) {
    const next: JSONSchema[] = []
    for (const b of cur) {
      if (seg === "[]") {
        if (b.items) next.push(...branches(b.items, root))
        if (b.prefixItems) for (const it of b.prefixItems) next.push(...branches(it, root))
      } else if (b.properties && b.properties[seg]) {
        next.push(...branches(b.properties[seg], root))
      } else if (b.additionalProperties && typeof b.additionalProperties === "object") {
        next.push(...branches(b.additionalProperties, root))
      }
    }
    cur = next
    if (!cur.length) break
  }
  return cur
}

function typeLabel(s: JSONSchema, root: JSONSchema): string {
  const bs = branches(s, root).filter((b) => b.type !== "null")
  const types = new Set<string>()
  for (const b of bs) {
    if (b.enum) types.add(b.enum.map(String).join("|"))
    else if (b.type) types.add(Array.isArray(b.type) ? b.type.join("|") : b.type)
    else if (b.properties) types.add("object")
  }
  return [...types].join(" | ").slice(0, 40)
}

// --- helpers ------------------------------------------------------------------------------

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
const anchored = (s: string) => `re:^${escapeRe(s)}$`
const quoteIfNeeded = (s: string) => (/^[\w\-./]+$/.test(s) ? s : JSON.stringify(s))

function pageElements(page: PageState | null) {
  return page?.elements ?? []
}

function uniq<T>(xs: T[]): T[] {
  return [...new Set(xs)]
}

function completion(label: string, type: string, detail?: string, apply?: string, boost = 0): Completion {
  return { label, type, detail, apply: apply ?? label, boost }
}

/** `{input}` and `{requests.<name>.<field>}` templates, quoted for a YAML value. */
function templateOptions(g: GraphCompletions): Completion[] {
  const out = g.inputs.map((i) => completion(`{${i}}`, "variable", "input", `"{${i}}"`, 1))
  for (const [name, fields] of Object.entries(g.requests ?? {})) {
    for (const f of fields) out.push(completion(`{requests.${name}.${f}}`, "variable", "request · fetched when used", `"{requests.${name}.${f}}"`, 2))
  }
  return out
}

// --- the source ---------------------------------------------------------------------------

export function yamlCompletionSource(getCtx: () => EditorContext) {
  return (cx: CompletionContext): CompletionResult | null => {
    const ec = getCtx()
    const doc = cx.state.doc.toString()
    const yc = yamlContextAt(doc, cx.pos)
    // Without the schema (API not restarted yet) we still offer graph + page suggestions.
    const root = ec.schema ? (ec.root === "workflow" ? ec.schema.workflow : ec.schema.node) : {}
    const shaperNames = ec.graph?.shapers ?? ec.schema?.shapers ?? []
    const allRoles = ec.schema?.roles ?? []
    const inStrategy = yc.path.includes("strategies") || yc.path.includes("visible") || yc.path.includes("hidden")

    let options: Completion[] = []

    if (yc.mode === "key") {
      const nodes = schemaAt(root, yc.path)
      const seen = new Set<string>()
      for (const n of nodes) {
        for (const [k, v] of Object.entries<JSONSchema>(n.properties ?? {})) {
          if (seen.has(k)) continue
          seen.add(k)
          const isContainer = branches(v, root).some((b) => b.type === "object" || b.type === "array" || b.properties)
          options.push(completion(k, "property", typeLabel(v, root), isContainer ? `${k}: ` : `${k}: `, v.default !== undefined ? -1 : 0))
        }
      }
      // At a sequence's level (e.g. the line under `actions:`), offer new items: `- <key>: `.
      const last = yc.path[yc.path.length - 1]
      const arrays = nodes.filter((n) => n.type === "array" || n.items)
      if (arrays.length) {
        const itemKeys = new Map<string, string>()
        for (const a of arrays) for (const it of branches(a.items ?? {}, root)) for (const [k, v] of Object.entries<JSONSchema>(it.properties ?? {})) itemKeys.set(k, typeLabel(v, root))
        if (last === "transitions" && ec.graph) {
          for (const n of ec.graph.nodes) options.push(completion(`- to: ${n}`, "keyword", "new transition", `- to: ${n}`, 3))
          options.push(completion("- when:", "keyword", "conditional transition", "- {when: {}, to: }", 2))
        } else if (last === "strategies") {
          options.push(completion("- {role: …}", "keyword", "new strategy", "- {role: ", 3))
          for (const k of itemKeys.keys()) options.push(completion(`- ${k}:`, "keyword", "new strategy", `- ${k}: `))
        } else {
          for (const [k, t] of itemKeys) options.push(completion(`- ${k}:`, "keyword", `new item · ${t}`, `- ${k}: `, 2))
        }
      }
      // Dict-of-things positions (nodes.<name>, targets.<name>): offer existing names.
      if (last === "nodes" && ec.graph) options.push(...ec.graph.nodes.map((n) => completion(n, "variable", "node", `${n}:`)))
      if (last === "targets" && ec.graph) options.push(...ec.graph.targets.map((t) => completion(t, "variable", "target", `${t}:`)))
      if (last === "requests" && ec.graph) options.push(...Object.keys(ec.graph.requests ?? {}).map((r) => completion(r, "variable", "request", `${r}:`)))
      if (!options.length && !cx.explicit) return null
    } else {
      const key = yc.key ?? ""
      const valueSchemas = schemaAt(root, yc.path.concat(key))
      const page = ec.page
      const g = ec.graph

      // enums / booleans from the schema
      for (const s of valueSchemas) {
        if (s.enum) options.push(...s.enum.map((v: unknown) => completion(String(v), "enum")))
        if (s.const !== undefined) options.push(completion(String(s.const), "enum"))
        if (s.type === "boolean") options.push(completion("true", "keyword"), completion("false", "keyword"))
      }

      // graph vocabulary
      if (["to", "start", "then"].includes(key) && g) options.push(...g.nodes.map((n) => completion(n, "variable", "node", n, 2)))
      if (key === "on_fail" && g) options.push(completion("repair", "keyword"), completion("fail", "keyword"), ...g.nodes.map((n) => completion(n, "variable", "node")))
      if (["target", "visible", "hidden"].includes(key) && g) {
        options.push(...g.targets.map((t) => completion(t, "variable", "target", t, 2)))
        if (key !== "target") options.push(completion("{role: …}", "text", "inline strategy", "{role: "))
      }
      if (key === "shape") options.push(...shaperNames.map((s) => completion(s, "function", "shaper")))
      if (["macro", "call"].includes(key) && g) options.push(...g.macros.map((m) => completion(m, "function", "macro")))
      const parent = yc.path[yc.path.length - 1]
      const requestNames = Object.keys(g?.requests ?? {})
      // fetch: {request: …} · condition request: {name: …} · fetch into
      if (g && (key === "request" || (key === "name" && parent === "request"))) options.push(...requestNames.map((r) => completion(r, "variable", "request", r, 3)))
      if (g && key === "into" && parent === "fetch") options.push(...requestNames.map((r) => completion(r, "variable", "vars name", r)))
      if (g && (TEMPLATE_KEYS.includes(key) || parent === "headers" || parent === "query")) options.push(...templateOptions(g))

      // live page
      if (page) {
        const els = pageElements(page)
        if (key === "dom_id") {
          for (const e of els) {
            if (!e.dom_id) continue
            const jsf = e.dom_id.includes(":")
            const pat = jsf ? `re::${escapeRe(e.dom_id.split(":").pop() ?? "")}$` : anchored(e.dom_id)
            options.push(completion(pat, "text", `${e.role} ${e.name ? `“${e.name}”` : e.id}`, quoteIfNeeded(pat), 3))
          }
        }
        if (key === "role") {
          const present = uniq(els.map((e) => e.role))
          options.push(...present.map((r) => completion(r, "enum", "on this page", r, 3)))
          options.push(...allRoles.filter((r) => !present.includes(r)).map((r) => completion(r, "enum")))
        }
        if (key === "name" && inStrategy) {
          for (const e of uniq(els.filter((e) => e.name).map((e) => `${e.role}\u0000${e.name}`))) {
            const [role, name] = e.split("\u0000")
            const pat = `/^${escapeRe(name)}$/i`
            options.push(completion(pat, "text", `${role} ${name.length > 40 ? name.slice(0, 40) + "…" : ""}`.trim(), JSON.stringify(pat), 3))
          }
        }
        if (key === "nearby") {
          for (const n of uniq(els.flatMap((e) => e.nearby))) options.push(completion(anchored(n), "text", "nearby text", quoteIfNeeded(anchored(n)), 3))
        }
        if (key === "landmark") {
          for (const l of uniq(els.map((e) => e.landmark).filter((x): x is string => !!x))) {
            options.push(completion(`re:${escapeRe(l.split("#").pop() ?? l)}`, "text", l, quoteIfNeeded(`re:${escapeRe(l.split("#").pop() ?? l)}`), 3))
          }
        }
        if (key === "url") {
          const path = page.route.split(" ?")[0]
          options.push(completion(`re:${escapeRe(path)}`, "text", "current route", quoteIfNeeded(`re:${escapeRe(path)}`), 3))
          options.push(completion(path, "text", "substring", quoteIfNeeded(path), 2))
        }
        if (key === "frame_url") {
          for (const f of page.frames.slice(1)) options.push(completion(`re:${escapeRe(f.route.split(" ?")[0])}`, "text", `f${f.index}`, quoteIfNeeded(`re:${escapeRe(f.route.split(" ?")[0])}`), 3))
        }
        if (key === "text") {
          for (const a of page.alerts) options.push(completion(a.slice(0, 50), "text", "alert", JSON.stringify(a.length > 60 ? a.slice(0, 60) : a), 3))
          for (const h of page.headings) options.push(completion(h.text.slice(0, 50), "text", `h${h.level}`, JSON.stringify(h.text), 2))
        }
        if (key === "option") {
          for (const e of els.filter((e) => e.role === "combobox")) for (const o of e.options) options.push(completion(o, "text", `${e.name || e.id}`, JSON.stringify(o), 2))
        }
        if (key === "page_type") options.push(...["search_form", "results", "detail", "login", "otp", "captcha", "challenge", "error"].map((p) => completion(p, "enum")))
      }

      // pattern prefixes, when nothing more specific applies
      if (!options.length && cx.explicit) {
        options.push(completion("re:", "keyword", "case-insensitive regex"), completion("/…/i", "keyword", "regex with flags", "/"))
      }
      if (!options.length) return null
    }

    return {
      from: yc.from,
      options,
      validFor: /^[^\s,{}[\]]*$/,
    }
  }
}
