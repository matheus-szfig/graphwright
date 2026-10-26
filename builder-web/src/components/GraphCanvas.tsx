import { useCallback, useEffect, useMemo } from "react"
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge as RFEdge,
  type Node as RFNode,
  type NodeProps,
} from "@xyflow/react"

import type { GraphView, Recognition } from "@/api"
import { NODE_W, toFlow, type NodeData, type NodeKind } from "@/layout"
import { Badge, Chip, Icon, type Tone } from "./ui"

const KIND: Record<NodeKind, { icon: string; tone: Tone; label: string; border: string }> = {
  step: { icon: "web", tone: "secondary", label: "STEP", border: "border-outline" },
  code: { icon: "code", tone: "primary", label: "CODE", border: "border-primary/50" },
  call: { icon: "account_tree", tone: "tertiary", label: "CALL", border: "border-tertiary/50" },
  success: { icon: "check_circle", tone: "success", label: "SUCCESS", border: "border-success/50" },
  outcome: { icon: "flag", tone: "warning", label: "OUTCOME", border: "border-warning/50" },
  failure: { icon: "error", tone: "danger", label: "FAILURE", border: "border-danger/50" },
}

function GraphNode({ data }: NodeProps<RFNode<NodeData>>) {
  const k = KIND[data.kind]
  const terminal = data.kind === "success" || data.kind === "outcome" || data.kind === "failure"
  const ring = data.failed
    ? "border-danger shadow-[0_0_0_1px_#f43f5e,0_0_0_6px_rgba(244,63,94,0.15)]"
    : data.current
      ? "border-secondary shadow-[0_0_0_1px_#06b6d4,0_8px_24px_-4px_rgba(6,182,212,0.3)]"
      : k.border
  return (
    <div
      className={`node-card rounded border bg-node text-left ${ring} ${data.current ? "node-running" : ""} ${data.isStart && !data.current && !data.failed ? "border-secondary/60" : ""}`}
      style={{ width: NODE_W }}
    >
      {/* The start node is the entry point: nothing flows into it, so no input port. */}
      {!data.isStart && <Handle type="target" position={Position.Top} />}
      {/* header bar — the start node gets the design's entry treatment: cyan-tinted header, emerald START tag */}
      <div className={`flex items-center gap-2 rounded-t border-b border-outline-variant px-3 py-2 ${data.isStart ? "bg-[#1e2e3a]" : "bg-node-header"}`}>
        <Icon
          name={data.isStart ? "play_circle" : k.icon}
          size={16}
          fill={data.isStart}
          className={data.failed ? "text-danger-soft" : data.current || data.isStart ? "text-secondary-soft" : "text-on-surface-variant"}
        />
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-on-surface">{data.label}</span>
        {data.isStart ? (
          <span className="label-caps inline-flex items-center gap-1 rounded bg-success px-1.5 py-0.5 text-[#032b1f] shadow-[0_0_12px_rgba(16,185,129,0.45)]">
            <Icon name="bolt" size={11} fill />
            start
          </span>
        ) : terminal || data.kind === "code" ? (
          <Badge tone={k.tone}>{k.label}</Badge>
        ) : null}
      </div>
      {/* body */}
      <div className="space-y-2 px-3 py-2">
        <p className="truncate text-[12px] text-on-surface-variant" title={data.intent}>
          {data.intent || (terminal ? (data.result ? JSON.stringify(data.result) : k.label.toLowerCase()) : "—")}
        </p>
        <div className="flex flex-wrap items-center gap-1">
          {!terminal && <Chip k="actions" v={data.actions} />}
          {!terminal && <Chip k="edges" v={data.transitions} tone="primary" />}
          {data.visits > 0 && <Chip k="visits" v={data.visits} tone="warning" />}
          {data.recognized !== null && (
            <span className={`label-caps ml-auto inline-flex items-center gap-1 ${data.recognized ? "text-success" : "text-faint"}`}>
              <span className="h-1.5 w-1.5 rounded-full bg-current" />
              {data.recognized ? "recognizes" : "no match"}
            </span>
          )}
        </div>
      </div>
      {!terminal && <Handle type="source" position={Position.Bottom} />}
    </div>
  )
}

const nodeTypes = { graphNode: GraphNode }

export interface GraphCanvasProps {
  view: GraphView
  current?: string
  status?: string
  failedNode?: string
  visits?: Record<string, number>
  recognized?: Record<string, Recognition>
  selected?: string
  onSelect: (node: string | undefined) => void
  onConnect: (source: string, target: string) => void
  onDeleteEdges: (edges: { source: string; target: string }[]) => void
  onDeleteNodes: (nodes: string[]) => void
}

export function GraphCanvas(p: GraphCanvasProps) {
  const layout = useMemo(
    () => toFlow(p.view, { current: p.current, recognized: p.recognized, failedNode: p.failedNode, visits: p.visits, status: p.status }),
    [p.view, p.current, p.recognized, p.failedNode, p.visits, p.status],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges)

  useEffect(() => {
    setNodes((prev) => {
      const pos = new Map(prev.map((n) => [n.id, n.position]))
      return layout.nodes.map((n) => ({ ...n, position: pos.get(n.id) ?? n.position, selected: n.id === p.selected }))
    })
    setEdges(layout.edges)
  }, [layout, p.selected, setNodes, setEdges])

  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source && c.target) p.onConnect(c.source, c.target)
    },
    [p],
  )

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={nodeTypes}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onConnect={onConnect}
      onNodeClick={(_, n) => p.onSelect(n.id)}
      onPaneClick={() => p.onSelect(undefined)}
      onEdgesDelete={(es: RFEdge[]) => p.onDeleteEdges(es.map((e) => ({ source: e.source, target: e.target })))}
      onNodesDelete={(ns) => p.onDeleteNodes(ns.map((n) => n.id))}
      deleteKeyCode={["Backspace", "Delete"]}
      fitView
      fitViewOptions={{ padding: 0.25, maxZoom: 1 }}
      colorMode="dark"
      defaultEdgeOptions={{ type: "default" }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1.5} color="rgba(51, 65, 85, 0.35)" />
      <Controls position="bottom-left" showInteractive />
      <MiniMap
        position="bottom-right"
        pannable
        zoomable
        bgColor="#0d111a"
        maskColor="rgba(12, 16, 23, 0.55)"
        nodeColor={(n) => ({ step: "#4cd7f6", code: "#c0c1ff", call: "#ddb7ff", success: "#10b981", outcome: "#f59e0b", failure: "#f43f5e" })[(n.data as NodeData).kind]}
        nodeStrokeWidth={0}
      />
    </ReactFlow>
  )
}
