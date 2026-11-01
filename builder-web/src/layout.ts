import dagre from "@dagrejs/dagre"
import type { Edge as RFEdge, Node as RFNode } from "@xyflow/react"

import type { GraphView } from "./api"

export const NODE_W = 268
// Reserve space for the card's header and a wrapped row of status chips.
export const NODE_H = 144
const NODE_GAP_X = 160
const NODE_GAP_Y = 112

export type NodeKind = "step" | "code" | "call" | "success" | "outcome" | "failure"

export interface NodeData extends Record<string, unknown> {
  label: string
  kind: NodeKind
  intent: string
  isStart: boolean
  hasIncoming: boolean
  current: boolean
  /** The current node AND the session is actively running it right now. */
  running: boolean
  recognized: boolean | null
  failed: boolean
  visits: number
  actions: number
  transitions: number
  timeout?: number
  result?: Record<string, unknown>
}

export function kindOf(node: GraphView["workflow"]["nodes"][string]): NodeKind {
  if (node.terminal) return node.terminal
  if (node.call) return "call"
  if (isCodeNode(node)) return "code"
  return "step"
}

const CODE_VERBS = ["script", "evaluate"]

/** A node whose actions are all code (`script` / `evaluate`) — shown as a code node. */
export function isCodeNode(node: GraphView["workflow"]["nodes"][string]): boolean {
  const actions = node.actions ?? []
  return actions.length > 0 && actions.every((a) => Object.keys(a).some((k) => CODE_VERBS.includes(k)))
}

// Edge colours follow the design: cyan for plain flow, indigo for conditional, rose for failure routes.
export const EDGE_COLOR = {
  always: "#4cd7f6",
  conditional: "#c0c1ff",
  fail: "#f43f5e",
  call: "#ddb7ff",
}

/** Keep the standard bottom-to-top Bezier edges. Move obstructing cards sideways.
 * Sample the same cubic used by React Flow at several card heights, since the
 * layout runs before measurement and chips can wrap. Rank/y positions stay fixed.
 */
function clearCurveSpace(nodes: RFNode<NodeData>[], edges: GraphView["edges"]) {
  const ordered = [...nodes].sort((a, b) => a.id.localeCompare(b.id))
  const byId = new Map(nodes.map(n => [n.id, n]))
  const sides = new Map<string, number>()
  const clearance = 48
  for (let pass = 0; pass < 40; pass++) {
    let moved = false
    for (const edge of edges) {
      const source = byId.get(edge.from)!, target = byId.get(edge.to)!
      if (source === target) continue
      for (const node of ordered) {
        if (node === source || node === target) continue
        let left = Infinity, right = -Infinity
        const sx = source.position.x + NODE_W / 2, tx = target.position.x + NODE_W / 2
        for (const height of [80, 104, NODE_H]) {
          const sy = source.position.y + height, ty = target.position.y
          const distance = ty - sy
          const offset = distance >= 0 ? distance / 2 : 6.25 * Math.sqrt(-distance)
          for (let i = 0; i <= 80; i++) {
            const t = i / 80, u = 1 - t
            const y = u ** 3 * sy + 3 * u ** 2 * t * (sy + offset) + 3 * u * t ** 2 * (ty - offset) + t ** 3 * ty
            if (y < node.position.y - clearance || y > node.position.y + NODE_H + clearance) continue
            const x = (u ** 3 + 3 * u ** 2 * t) * sx + (3 * u * t ** 2 + t ** 3) * tx
            left = Math.min(left, x - clearance)
            right = Math.max(right, x + clearance)
          }
          if (edge.label !== "always" && (sy + ty) / 2 >= node.position.y - 28 - clearance &&
              (sy + ty) / 2 <= node.position.y + NODE_H + 28 + clearance) {
            const halfLabel = (edge.label.length * 7 + 24) / 2
            left = Math.min(left, (sx + tx) / 2 - halfLabel - clearance)
            right = Math.max(right, (sx + tx) / 2 + halfLabel + clearance)
          }
        }
        if (node.position.x + NODE_W <= left || node.position.x >= right) continue
        const key = `${edge.from}\0${edge.to}\0${node.id}`
        const side = sides.get(key) ?? (node.position.x + NODE_W / 2 <= (left + right) / 2 ? -1 : 1)
        sides.set(key, side)
        node.position.x = side < 0 ? left - NODE_W - 1 : right + 1
        moved = true
      }
    }
    // Moving a card out of a curve must not place it on another card in its row.
    const byX = [...ordered].sort((a, b) => a.position.x - b.position.x || a.id.localeCompare(b.id))
    for (let i = 0; i < byX.length; i++) for (let j = i + 1; j < byX.length; j++) {
      const a = byX[i], b = byX[j]
      if (Math.abs(a.position.y - b.position.y) >= NODE_H) continue
      if (b.position.x < a.position.x + NODE_W + NODE_GAP_X) {
        b.position.x = a.position.x + NODE_W + NODE_GAP_X
        moved = true
      }
    }
    if (!moved) break
  }
  const minX = Math.min(64, ...nodes.map(n => n.position.x))
  for (const node of nodes) node.position.x += 64 - minX
}

export function toFlow(
  view: GraphView,
  opts: { current?: string; recognized?: Record<string, { ok: boolean }>; failedNode?: string; visits?: Record<string, number>; status?: string } = {},
): { nodes: RFNode<NodeData>[]; edges: RFEdge[] } {
  const g = new dagre.graphlib.Graph({ multigraph: true })
  g.setDefaultEdgeLabel(() => ({}))
  // Vertical flow: start at the top, outcomes at the bottom.
  g.setGraph({ rankdir: "TB", nodesep: NODE_GAP_X, ranksep: NODE_GAP_Y, edgesep: 120, marginx: 64, marginy: 64 })
  // Stable insertion order makes a reload independent of YAML mapping order.
  const names = Object.keys(view.workflow.nodes).sort()
  const validEdges = view.edges.filter((e) => e.from in view.workflow.nodes && e.to in view.workflow.nodes)
  for (const name of names) g.setNode(name, { width: NODE_W, height: NODE_H })
  const counts = new Map<string, number>()
  const routedEdges = validEdges.map(e => {
    const key = `${e.from}->${e.to}`
    const n = (counts.get(key) ?? 0) + 1
    counts.set(key, n)
    return { ...e, id: `${key}#${n}` }
  })
  for (const e of [...routedEdges].sort((a, b) => a.id.localeCompare(b.id))) {
    // Labels are obstacles too: give Dagre their footprint so a bypass lane
    // cannot be squeezed against a card or another transition's label.
    g.setEdge(e.from, e.to, {
      width: e.label === "always" ? 0 : e.label.length * 7 + 24,
      height: e.label === "always" ? 0 : 28,
      labelpos: "c",
    }, e.id)
  }
  dagre.layout(g)

  const nodes: RFNode<NodeData>[] = Object.entries(view.workflow.nodes).map(([name, node]) => {
    const pos = g.node(name)
    return {
      id: name,
      type: "graphNode",
      position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 },
      data: {
        label: name,
        kind: kindOf(node),
        intent: node.intent ?? "",
        isStart: view.workflow.start === name,
        hasIncoming: validEdges.some(e => e.to === name),
        current: opts.current === name,
        running: opts.current === name && opts.status === "running",
        recognized: opts.recognized ? (opts.recognized[name]?.ok ?? null) : null,
        failed: opts.failedNode === name,
        visits: opts.visits?.[name] ?? 0,
        actions: node.actions?.length ?? 0,
        transitions: node.transitions?.length ?? 0,
        timeout: node.timeout_s,
        result: node.result,
      },
    }
  })

  clearCurveSpace(nodes, [...validEdges].sort((a, b) => `${a.from}\0${a.to}\0${a.label}`.localeCompare(`${b.from}\0${b.to}\0${b.label}`)))
  const edges: RFEdge[] = routedEdges.map((e) => {
    const kind = e.label === "on_fail" ? "fail" : e.label === "always" ? "always" : e.label.startsWith("call ") ? "call" : "conditional"
    const color = EDGE_COLOR[kind]
    const active = opts.current === e.from && opts.status === "running"
    return {
      id: e.id,
      type: "default",
      source: e.from,
      target: e.to,
      label: e.label === "always" ? undefined : e.label,
      className: active ? "edge-active" : undefined,
      style: { stroke: color, strokeOpacity: 0.85, strokeDasharray: kind === "fail" ? "6 6" : undefined },
      labelStyle: { fill: color },
      labelBgPadding: [6, 3] as [number, number],
      labelBgBorderRadius: 4,
      data: { label: e.label, kind },
    }
  })
  return { nodes, edges }
}
