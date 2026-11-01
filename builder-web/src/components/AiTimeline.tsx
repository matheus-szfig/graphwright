import { useState, type ReactNode } from "react"
import type { AiEvent, AiProposal } from "../ai"
import { Icon } from "./ui"
import { AiMarkdown } from "./AiMarkdown"
import { AiCodeBlock } from "./AiCodeBlock"

export type TurnStatus = "running" | "completed" | "stopped" | "failed" | "limited"
export interface AiTurn {
  id: string
  message: string
  status: TurnStatus
  startedAt: number
  finishedAt?: number
  events: AiEvent[]
  /** Model that answered this turn; the model can change between turns. */
  model?: string
}

export function duration(ms: number) {
  const seconds = Math.max(0, Math.floor(ms / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}

const TOOL_LABELS: Record<string, string> = {
  explore_graph: "Explore graph", list_context: "Find relevant context", read_context: "Read context details",
  search_context: "Search context content",
  get_session: "Inspect session", get_events: "Read session events", redetect: "Observe page",
  propose_edits: "Prepare graph changes", target_from_element: "Create target proposal",
  test_script: "Test JavaScript", test_extractor: "Test extractor", propose_action: "Prepare action review",
  propose_browser_action: "Prepare browser action",
}

export function toolLabel(name?: string) { return TOOL_LABELS[name || ""] || name || "Tool" }

function JsonDetail({ label, value }: { label: string; value?: string }) {
  if (!value) return null
  return <div className="mt-2 min-w-0"><AiCodeBlock text={value} language="json" label={label} maxHeight="max-h-64" /></div>
}

function ActivityGroup({ children, tools, running, failed }: { children: ReactNode; tools: number; running: boolean; failed: boolean }) {
  const [expanded, setExpanded] = useState<boolean | null>(null)
  return <details open={expanded ?? (running || failed)} className="rounded-lg border border-outline-variant bg-recess/50">
    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[11px] text-muted [&::-webkit-details-marker]:hidden"
      aria-label="Execution activity" onClick={e => { e.preventDefault(); setExpanded(!(expanded ?? (running || failed))) }}>
      <Icon name={(expanded ?? (running || failed)) ? "expand_more" : "chevron_right"} size={14} />
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${running ? "animate-pulse bg-secondary" : failed ? "bg-warning" : "bg-success"}`} />
      <span className="font-medium text-on-surface-variant">Execution activity</span>
      <span className="mono ml-auto">{tools} tool call{tools === 1 ? "" : "s"}</span>
    </summary>
    <ol className="space-y-1 border-t border-outline-variant px-3 py-2" aria-label="Turn activity">{children}</ol>
  </details>
}

export function TurnView({ turn, now, onRetry, renderProposal }: {
  turn: AiTurn; now: number; onRetry: (text: string) => void
  renderProposal: (proposal: AiProposal, index: number) => ReactNode
}) {
  const running = turn.status === "running"
  const elapsed = (turn.finishedAt ?? now) - turn.startedAt
  const toolEnds = new Map(turn.events.filter(e => e.kind === "tool_end").map(e => [e.tool_id, e]))
  const modelEnds = new Map(turn.events.filter(e => e.kind === "model_done").map(e => [e.round, e]))
  const done = turn.events.find(e => e.kind === "done")
  const status = {
    running: ["progress_activity", "Working", "text-secondary-soft"],
    completed: ["check_circle", "Completed", "text-success"],
    stopped: ["stop_circle", "Stopped", "text-muted"],
    failed: ["error", "Failed", "text-danger-soft"],
    limited: ["info", "Tool limit reached", "text-warning"],
  }[turn.status]
  const groups: { kind: "activity" | "content"; indices: number[] }[] = []
  turn.events.forEach((event, index) => {
    if (["tool_end", "model_done", "done", "heartbeat"].includes(event.kind)) return
    const kind = ["context", "activity", "tool_start"].includes(event.kind) ? "activity" : "content"
    const last = groups[groups.length - 1]
    if (kind === "activity" && last?.kind === kind) last.indices.push(index)
    else groups.push({ kind, indices: [index] })
  })
  const time = new Date(turn.startedAt).toLocaleTimeString([], { hour12: false })
  return <article className="space-y-5 py-5" aria-label="Assistant turn">
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-primary-soft/30 bg-primary-soft/10 text-primary-soft"><Icon name="person" size={17} /></span>
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex items-center gap-2 text-xs"><b className="text-primary-soft">You</b><time className="mono text-[10px] text-faint" dateTime={new Date(turn.startedAt).toISOString()}>{time}</time><span className="label-caps rounded border border-outline-variant bg-surface-container px-1.5 py-0.5 text-muted">Prompt</span></div>
        <div className="rounded-xl rounded-tl-sm border border-outline-variant bg-surface-low/90 px-4 py-3"><AiMarkdown text={turn.message} /></div>
      </div>
    </div>
    <div className="flex items-start gap-3">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-secondary/30 bg-secondary/10 text-secondary-soft"><Icon name="smart_toy" size={17} /></span>
      <div className="min-w-0 flex-1 space-y-3">
    <div className="flex flex-wrap items-center gap-2 text-xs">
      <b className="text-secondary-soft">Assistant</b>
      {turn.model && <span className="mono truncate rounded border border-outline-variant bg-surface-container px-1.5 py-0.5 text-[10px] text-muted" title="Model used for this turn">{turn.model}</span>}
      <span className={`ml-auto inline-flex items-center gap-1.5 ${status[2]}`}>
        <Icon name={status[0]} size={14} className={running ? "animate-spin" : ""} />{status[1]}
        <span className="mono text-[11px] tabular-nums text-muted">{duration(elapsed)}</span>
      </span>
    </div>
      {!turn.events.length && <p className="flex items-center gap-2 py-1 text-xs text-muted"><Icon name="progress_activity" size={14} className="animate-spin" />Preparing context…</p>}
      {groups.map(group => {
        const rows = group.indices.map(i => {
        const event = turn.events[i]
        const key = `${i}:${event.sequence ?? "local"}`
        if (event.kind === "tool_end" || event.kind === "model_done" || event.kind === "done" || event.kind === "heartbeat") return null
        if (event.kind === "text") return <div key={key} className="rounded-xl rounded-tl-sm border border-outline-variant bg-surface-low/50 px-4 py-3">
          {modelEnds.get(event.round)?.reason === "output_limit" && <p className="mb-2 text-[11px] text-warning">Partial response · output limit reached</p>}
          <AiMarkdown text={event.text || ""} /></div>
        if (event.kind === "tool_start") {
          const end = toolEnds.get(event.tool_id)
          const active = !end && running
          const failed = end?.ok === false
          return <li key={key}>
            <details className={`rounded-lg border px-3 py-2 ${active ? "border-secondary/40 bg-secondary/5" : failed ? "border-danger/30 bg-danger/5" : "border-outline-variant bg-surface-container"}`}>
              <summary className="flex cursor-pointer list-none items-center gap-2 text-xs [&::-webkit-details-marker]:hidden" aria-label={`${toolLabel(event.name)} — ${active ? "Running" : failed ? "Failed" : end ? "Completed" : "Interrupted"}`}>
                <Icon name={active ? "progress_activity" : failed ? "error" : end ? "check_circle" : "stop_circle"} size={15}
                  className={active ? "animate-spin text-secondary-soft" : failed ? "text-danger-soft" : end ? "text-success" : "text-muted"} />
                <span className="min-w-0 flex-1 truncate text-on-surface">{toolLabel(event.name)}</span>
                <span className="shrink-0 text-[10px] text-muted">{active ? "Running" : failed ? "Failed" : end ? duration(end.duration_ms || 0) : "Interrupted"}</span>
                <Icon name="unfold_more" size={14} className="text-faint" />
              </summary>
              <p className="mono mt-2 text-[10px] text-faint">{event.name} · call {event.tool_id}</p>
              <JsonDetail label="Arguments" value={event.arguments} />
              <JsonDetail label={failed ? "Error" : "Result"} value={end?.result} />
              {!end && <p className="mt-2 text-xs text-muted">{active ? "Waiting for the tool result…" : "No result returned before this turn ended."}</p>}
            </details>
          </li>
        }
        if (event.kind === "activity") {
          const end = modelEnds.get(event.round)
          const isModel = event.phase === "model" || event.round != null
          const completed = !!end || turn.status === "completed"
          const active = !completed && running
          return <li key={key} className="flex items-center gap-2 py-1 text-[11px] text-muted">
            <Icon name={active ? "progress_activity" : completed ? "check" : "stop_circle"} size={14} className={active ? "animate-spin text-secondary-soft" : "text-faint"} />
            <span>{!isModel ? event.text || "Activity" : end?.reason === "output_limit" ? "Output limit reached" : end ? "Model responded" : active ? event.text : completed ? "Model responded (timing unavailable)" : "Model request interrupted"}{event.round ? ` · round ${event.round}` : ""}</span>
            {end && <span className="ml-auto mono text-[10px]">{duration(end.duration_ms || 0)}</span>}
          </li>
        }
        if (event.kind === "context") return <li key={key}><details className="text-[11px] text-muted">
          <summary className="cursor-pointer py-1">Context prepared{event.context_stats && <span className="ml-2 text-faint">
            {event.context_stats.selected_chars.toLocaleString()} characters · {event.context_stats.included_resources} resources
            {event.context_stats.source_chars > event.context_stats.selected_chars && ` · ${Math.round((1 - event.context_stats.selected_chars / event.context_stats.source_chars) * 100)}% smaller`}
          </span>}</summary>
          <JsonDetail label="AI-visible context" value={JSON.stringify(event.context, null, 2)} />
        </details></li>
        if (event.kind === "proposal" && event.proposal) return <div key={key}>{renderProposal(event.proposal, i)}</div>
        if (event.kind === "steer") return <div key={key} aria-label="Message sent while working" className="ml-auto max-w-[85%] rounded-xl rounded-tr-sm border border-primary-soft/30 bg-primary-soft/10 px-3 py-2">
          <p className="label-caps mb-1 flex items-center gap-1 text-primary-soft"><Icon name="person" size={12} />You · while working</p>
          <AiMarkdown text={event.text || ""} /></div>
        if (event.kind === "error" || event.kind === "cancelled") return <div key={key} role={event.kind === "error" ? "alert" : undefined} className={`rounded-lg p-3 text-xs ${event.kind === "error" ? "bg-danger/10 text-danger-soft" : "bg-surface-high text-muted"}`}>
          {event.text}
        </div>
        return null
        })
        const calls = group.indices.map(i => turn.events[i]).filter(event => event.kind === "tool_start")
        const failed = calls.some(event => toolEnds.get(event.tool_id)?.ok === false || (!running && !toolEnds.has(event.tool_id)))
        return group.kind === "activity" ? <ActivityGroup key={group.indices[0]} tools={calls.length} running={running} failed={failed}>{rows}</ActivityGroup> : <div key={group.indices[0]}>{rows}</div>
      })}
    {done?.usage && <p className="pl-3 text-[10px] text-faint">{done.usage.input_tokens.toLocaleString()} input{done.usage.cached_input_tokens ? ` (${done.usage.cached_input_tokens.toLocaleString()} cached)` : ""} · {done.usage.output_tokens.toLocaleString()} output tokens</p>}
    {(turn.status === "failed" || turn.status === "stopped" || turn.status === "limited") && <button type="button"
      className="inline-flex items-center gap-1 text-xs text-primary-soft hover:underline" onClick={() => onRetry(turn.message)}>
      <Icon name="edit" size={14} />Reuse message
    </button>}
      </div>
    </div>
  </article>
}
