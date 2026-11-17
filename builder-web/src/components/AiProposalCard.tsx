import { useState } from "react"
import type { AiProposal } from "../ai"
import { AiCodeBlock } from "./AiCodeBlock"
import { Icon, cls } from "./ui"

export type ProposalResolution = "saved" | "patched" | "executed" | "discarded"

export function AiProposalCard({ proposal, resolution, busy, dirty, sessionId, inputs, onInputs, onApply, onDiscard }: {
  proposal: AiProposal; resolution?: ProposalResolution; busy: boolean; dirty: boolean; sessionId?: string
  inputs: Record<string, string>; onInputs: (inputs: Record<string, string>) => void
  onApply: (patch: boolean) => void; onDiscard: () => void
}) {
  const [view, setView] = useState<"diff" | "yaml">("diff")
  const graph = proposal.kind === "graph"
  const added = proposal.diff.split("\n").filter(line => line.startsWith("+") && !line.startsWith("+++")).length
  const removed = proposal.diff.split("\n").filter(line => line.startsWith("-") && !line.startsWith("---")).length
  const title = graph ? "Proposed graph changes" : `Review ${proposal.action || "action"}${proposal.name ? ` · ${proposal.name}` : ""}`
  const status = resolution ? { saved: "Saved", patched: "Saved · patch requested", executed: "Executed", discarded: "Discarded" }[resolution]
    : proposal.errors.length ? "Review diagnostics" : "Ready to apply"
  return <section id={`ai-proposal-${proposal.id}`} className={`ai-proposal overflow-hidden rounded-xl border ${resolution ? "border-outline-strong" : "border-primary-soft/40 shadow-[0_0_0_1px_rgba(192,193,255,0.08)]"} bg-surface-low`} aria-label={title}>
    <div className="flex flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-high/40 px-4 py-3">
      <span className="label-caps flex items-center gap-1 rounded border border-secondary/20 bg-secondary/10 px-2 py-1 text-secondary-soft"><Icon name={graph ? "account_tree" : "play_arrow"} size={13} />{graph ? "Graph update" : "Live action"}</span>
      <span className="min-w-0 flex-1 truncate text-xs font-semibold text-on-surface" title={proposal.graph}>{proposal.graph}.yaml</span>
      <span className={`label-caps flex items-center gap-1 rounded-full border px-2 py-1 ${proposal.errors.length ? "border-warning/30 bg-warning/10 text-warning" : "border-success/25 bg-success/10 text-success"}`}>
        <Icon name={proposal.errors.length ? "warning" : "check_circle"} size={12} /><span>{proposal.errors.length ? `${proposal.errors.length} diagnostics` : graph ? "Schema valid" : "Review required"}</span>
      </span>
    </div>
    <div className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="min-w-0 flex-1 text-[13px] font-semibold text-on-surface">{title}</h3>
        <span className={`label-caps rounded-full border px-2 py-1 ${resolution ? "border-outline-strong text-muted" : "border-primary-soft/30 bg-primary-soft/10 text-primary-soft"}`}>{status}</span>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-[11px]">
        <div className="flex rounded-md border border-outline-variant bg-recess p-0.5" role="group" aria-label="Proposal view">
          <button type="button" className={`rounded px-2 py-1 ${view === "diff" ? "bg-surface-high text-on-surface" : "text-muted hover:text-on-surface"}`} aria-pressed={view === "diff"} onClick={() => setView("diff")}>Diff with graph</button>
          {graph && proposal.yaml && <button type="button" className={`rounded px-2 py-1 ${view === "yaml" ? "bg-surface-high text-on-surface" : "text-muted hover:text-on-surface"}`} aria-pressed={view === "yaml"} onClick={() => setView("yaml")}>Proposed YAML</button>}
        </div>
        <span className="mono ml-auto text-success">+{added}</span><span className="mono text-danger-soft">−{removed}</span><span className="text-faint">lines</span>
      </div>
      <AiCodeBlock text={view === "yaml" && proposal.yaml ? proposal.yaml : proposal.diff} language={view === "yaml" ? "yaml" : "diff"} label={view === "yaml" ? "Proposed YAML" : "Graph diff"} />
      {!!proposal.errors.length && <div className="space-y-1 rounded-lg border border-warning/20 bg-warning/5 p-3 text-xs text-warning" aria-label="Proposal diagnostics">
        {proposal.errors.map((message, i) => <p key={i} className="break-words">{message}</p>)}
      </div>}
      {!resolution && Object.entries(proposal.new_inputs || {}).map(([name, spec]) => <label key={name} className="flex flex-col gap-1 text-xs text-muted">
        {name} · local value for session patch{spec.required !== false ? " (required)" : ""}
        <input className={cls.input} autoComplete="off" type={spec.pii !== false || spec.credential || ["secure", "password"].includes(spec.type || "") ? "password" : "text"}
          value={inputs[name] || ""} onChange={e => onInputs({ ...inputs, [name]: e.target.value })} />
      </label>)}
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-t border-outline-variant bg-surface-container/60 px-4 py-3">
      <p className={`flex min-w-0 flex-1 items-start gap-2 text-[11px] ${dirty && !resolution ? "text-warning" : "text-muted"}`}>
        <Icon name={dirty && !resolution ? "warning" : "info"} size={15} className="shrink-0 text-secondary-soft" />
        {resolution ? (resolution === "discarded" ? "This proposal was discarded. The graph was not changed." : "This proposal has been applied.")
          : dirty ? "Save or discard your editor drafts before applying AI changes."
          : graph ? "Save the reviewed changes to the graph. Patch the session separately when needed." : "Runs this action once in the attached session after your review."}
      </p>
      {!resolution && <div className="flex flex-wrap items-center gap-2">
        <button type="button" className={cls.btn} disabled={busy} onClick={onDiscard}><Icon name="close" size={14} />Discard</button>
        {graph && sessionId && <button type="button" className={cls.btn} disabled={busy || dirty || !!proposal.errors.length} onClick={() => onApply(true)}><Icon name="sync" size={14} />Save & patch session</button>}
        <button type="button" className="inline-flex items-center gap-1.5 rounded-lg border border-primary-soft/30 bg-primary-soft px-3 py-1.5 text-xs font-semibold text-recess hover:brightness-110 disabled:opacity-40" disabled={busy || dirty} onClick={() => onApply(false)}>
          <Icon name="check_circle" size={15} />{graph ? "Save graph" : "Execute reviewed action"}
        </button>
      </div>}
    </div>
  </section>
}
