// Where is the cursor in a YAML document? Returns the key path (block mapping keys,
// `[]` for sequence items, flow-mapping keys) and whether a key or a value is being
// typed. Deliberately approximate — it only has to be right for the graph YAML style
// (block mappings with flow `{a: b}` items), not for every YAML document.

export interface YamlContext {
  path: string[]
  mode: "key" | "value"
  key?: string
  prefix: string
  from: number
}

const KEY_RE = /^("[^"]*"|'[^']*'|[^\s:#{}[\],-][^:#{}[\],]*?)\s*:(\s|$)/

function unquote(s: string): string {
  s = s.trim()
  if ((s.startsWith('"') && s.endsWith('"')) || (s.startsWith("'") && s.endsWith("'"))) return s.slice(1, -1)
  return s
}

interface Frame { indent: number; key: string }

export function yamlContextAt(doc: string, pos: number): YamlContext {
  const before = doc.slice(0, pos)
  const lines = before.split("\n")
  const cur = lines.pop() ?? ""
  const stack: Frame[] = []

  const popTo = (indent: number) => {
    while (stack.length && stack[stack.length - 1].indent >= indent) stack.pop()
  }

  // Block structure from the lines above the cursor.
  for (const raw of lines) {
    if (!raw.trim() || raw.trimStart().startsWith("#")) continue
    let indent = raw.length - raw.trimStart().length
    let rest = raw.slice(indent)
    popTo(indent)
    while (rest.startsWith("- ") || rest === "-") {
      stack.push({ indent, key: "[]" })
      indent += 2
      rest = rest.slice(2)
    }
    const m = KEY_RE.exec(rest)
    if (m) stack.push({ indent, key: unquote(m[1]) })
  }

  // Current line.
  let indent = cur.length - cur.trimStart().length
  let rest = cur.slice(indent)
  const inIndentation = cur.trim() === "" || pos - (before.length - cur.length) <= indent
  popTo(indent)
  while (rest.startsWith("- ") || rest === "-") {
    stack.push({ indent, key: "[]" })
    indent += 2
    rest = rest.slice(2)
  }
  const path = stack.map((f) => f.key)
  if (inIndentation) return { path, mode: "key", prefix: "", from: pos }

  // Flow-aware scan of the text on this line.
  const flow: { key: string | null; pushed: number }[] = []
  let currentKey: string | null = null
  let mode: "key" | "value" = "key"
  let token = ""
  let quote: string | null = null
  const flowPath: string[] = []

  for (let i = 0; i < rest.length; i++) {
    const ch = rest[i]
    if (quote) {
      token += ch
      if (ch === quote) quote = null
      continue
    }
    if (ch === '"' || ch === "'") { quote = ch; token += ch; continue }
    if (ch === "{" || ch === "[") {
      let pushed = 0
      if (currentKey) { flowPath.push(currentKey); pushed++ }
      if (ch === "[") { flowPath.push("[]"); pushed++ }
      flow.push({ key: currentKey, pushed })
      currentKey = null; mode = "key"; token = ""
      continue
    }
    if (ch === "}" || ch === "]") {
      const f = flow.pop()
      if (f) for (let k = 0; k < f.pushed; k++) flowPath.pop()
      currentKey = null; mode = "key"; token = ""
      continue
    }
    if (ch === "," && flow.length) { currentKey = null; mode = "key"; token = ""; continue }
    if (ch === ":" && mode === "key" && (i + 1 >= rest.length || /\s/.test(rest[i + 1]) || flow.length)) {
      currentKey = unquote(token); mode = "value"; token = ""
      continue
    }
    token += ch
  }

  const prefix = token.replace(/^\s+/, "")
  const fullPath = path.concat(flowPath)
  if (mode === "value" && currentKey !== null) {
    return { path: fullPath, mode: "value", key: currentKey, prefix, from: pos - prefix.length }
  }
  return { path: fullPath, mode: "key", prefix, from: pos - prefix.length }
}
