import type { GraphView, SessionSummary } from "@/api"
import { Icon } from "./ui"

export interface PlayerProps {
  view: GraphView
  session: SessionSummary | null
  busy: boolean
  selectedNode?: string
  missingInputs: string[]
  fast: boolean
  onToggleFast: () => void
  onStart: () => void
  onStep: () => void
  onRun: () => void
  onObserve: () => void
  onPatch: () => void
  onReset: (node: string) => void
  onClose: () => void
}

const STATUS: Record<string, { label: string; dot: string }> = {
  idle: { label: "IDLE", dot: "bg-faint" },
  paused: { label: "PAUSED", dot: "bg-secondary-soft" },
  running: { label: "RUNNING", dot: "bg-warning animate-pulse" },
  done: { label: "DONE", dot: "bg-success" },
  failed: { label: "FAILED", dot: "bg-danger" },
  needs_repair: { label: "NEEDS REPAIR", dot: "bg-danger" },
}

function HudButton({ icon, label, title, onClick, disabled, tone = "default", shortcut }: {
  icon: string
  label?: string
  title: string
  onClick: () => void
  disabled?: boolean
  tone?: "default" | "primary" | "danger"
  shortcut?: string
}) {
  const tones = {
    default: "bg-surface-container text-on-surface-variant hover:bg-surface-high hover:text-on-surface",
    primary: "bg-primary text-white shadow-[0_0_14px_rgba(99,102,241,0.45)] hover:brightness-110",
    danger: "text-danger-soft hover:bg-danger/15",
  }
  return (
    <button
      className={`inline-flex h-8 items-center gap-1.5 rounded-lg px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-35 ${tones[tone]}`}
      onClick={onClick}
      disabled={disabled}
      title={shortcut ? `${title} (${shortcut})` : title}
    >
      <Icon name={icon} size={18} />
      {label && <span className="font-medium">{label}</span>}
    </button>
  )
}

/** Floating run/debug toolbar, top-center of the canvas (design: "Canvas Control HUDs"). */
export function Player(p: PlayerProps) {
  const s = p.session
  const canAct = !!s && !p.busy && s.status !== "done" && s.status !== "failed"
  const st = s ? STATUS[s.status] ?? STATUS.idle : { label: "READY", dot: "bg-success" }

  return (
    <div className="hud pointer-events-auto flex items-center gap-1 p-1.5">
      {!s ? (
        <>
          <button
            className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-primary-soft px-3 text-xs font-semibold text-[#1000a9] hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
            disabled={p.busy || p.missingInputs.length > 0}
            title={p.missingInputs.length ? `fill inputs: ${p.missingInputs.join(", ")}` : `start a session — opens ${p.view.workflow.entry_url ?? "nothing (no entry_url)"}`}
            onClick={p.onStart}
          >
            <Icon name="play_arrow" size={18} fill />
            Start
          </button>
          <Divider />
          <HudButton icon={p.fast ? "fast_forward" : "slow_motion_video"} label={p.fast ? "fast" : "human"} title={p.fast ? "fewer humanisation pauses — click for realistic timing" : "realistic humanisation timing — click for fast"} onClick={p.onToggleFast} />
        </>
      ) : (
        <>
          <HudButton icon="step_over" label="Step" title="run the current node once" shortcut="F10" onClick={p.onStep} disabled={!canAct} tone="primary" />
          <HudButton icon="play_arrow" label="Run" title="run until a terminal node or a failure" shortcut="F5" onClick={p.onRun} disabled={!canAct} />
          <Divider />
          <HudButton icon="visibility" title="observe the page again" onClick={p.onObserve} disabled={p.busy} />
          <HudButton icon="sync" title="patch: reload the graph from disk into the running session" onClick={p.onPatch} disabled={p.busy} />
          <HudButton
            icon="my_location"
            title={p.selectedNode ? `continue from ${p.selectedNode}` : "select a node on the canvas to jump there"}
            onClick={() => p.selectedNode && p.onReset(p.selectedNode)}
            disabled={p.busy || !p.selectedNode}
          />
          <Divider />
          <HudButton icon="stop" title="close the session and its browser context" onClick={p.onClose} tone="danger" />
        </>
      )}
      <Divider />
      <div className="flex h-8 items-center gap-2 rounded-lg bg-surface-container px-2.5" title={s?.url ?? ""}>
        <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
        <span className="label-caps text-on-surface-variant">{st.label}</span>
        {s && (
          <>
            <span className="text-outline">·</span>
            <span className="mono max-w-[180px] truncate text-[11px] text-secondary-soft">{s.node}</span>
          </>
        )}
      </div>
    </div>
  )
}

function Divider() {
  return <span className="mx-0.5 h-5 w-px bg-outline-variant" />
}
