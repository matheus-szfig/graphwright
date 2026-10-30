import dagre from "@dagrejs/dagre"
import type { Edge as RFEdge, Node as RFNode } from "@xyflow/react"

import type { GraphView } from "./api"

export const NODE_W = 268
export const NODE_H = 112

export type NodeKind = "step" | "code" | "call" | "success" | "outcome" | "failure"

export interface NodeData extends Record<string, unknown> {
  label: string
  kind: NodeKind
  intent: string
  isStart: boolean
  hasIncoming: boolean
  running: boolean
  current: boolean
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

export function toFlow(
  view: GraphView,
  opts: { current?: string; recognized?: Record<string, { ok: boolean }>; failedNode?: string; visits?: Record<string, number>; status?: string } = {},
): { nodes: RFNode<NodeData>[]; edges: RFEdge[] } {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  // Vertical flow: start at the top, outcomes at the bottom.
  g.setGraph({ rankdir: "TB", nodesep: 56, ranksep: 90, marginx: 40, marginy: 40 })
  for (const name of Object.keys(view.workflow.nodes)) g.setNode(name, { width: NODE_W, height: NODE_H })
  for (const e of view.edges) if (e.from !== e.to) g.setEdge(e.from, e.to)
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
        current: opts.current === name,
        running: opts.current === name && opts.status === "running",
        hasIncoming: view.edges.some(e => e.to === name),
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

  const seen = new Map<string, number>()
  const edges: RFEdge[] = view.edges.map((e) => {
    const key = `${e.from}->${e.to}`
    const n = (seen.get(key) ?? 0) + 1
    seen.set(key, n)
    const kind = e.label === "on_fail" ? "fail" : e.label === "always" ? "always" : e.label.startsWith("call ") ? "call" : "conditional"
    const color = EDGE_COLOR[kind]
    const active = opts.current === e.from && opts.status === "running"
    return {
      id: `${key}#${n}`,
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
