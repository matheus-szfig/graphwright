import { useState, type ReactNode } from "react"
import type { BuilderEvent } from "@/api"
import { Icon } from "./ui"

const button = "inline-flex items-center justify-center gap-1 rounded border border-outline-variant px-2 py-1 text-xs hover:bg-surface-high disabled:opacity-40"

async function copyJson(value: unknown) {
  const text = JSON.stringify(value, null, 2)
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  const field = document.createElement("textarea")
  field.value = text
  field.style.position = "fixed"
  field.style.opacity = "0"
  document.body.append(field)
  const previous = document.activeElement as HTMLElement | null
  try {
    field.select()
    if (!document.execCommand("copy")) throw new Error("Clipboard unavailable")
  } finally {
    field.remove()
    previous?.focus()
  }
}

function CopyButton({ value, label, iconOnly = false }: { value: unknown; label: string; iconOnly?: boolean }) {
  const [status, setStatus] = useState("")
  return <span className={iconOnly ? "inline-flex" : "inline-flex items-center gap-2"}>
    <button type="button" className={iconOnly
      ? "inline-flex h-6 w-6 shrink-0 items-center justify-center rounded text-muted hover:bg-surface-high hover:text-on-surface focus-visible:outline focus-visible:outline-primary"
      : button} aria-label={label} title={status || label} onClick={async () => {
      try {
        await copyJson(value)
        setStatus("Copied")
      } catch {
        setStatus("Copy failed; check clipboard permission")
      }
    }}><Icon name={status === "Copied" ? "check" : status ? "error" : "content_copy"} size={14} />{!iconOnly && label}</button>
    <span role="status" className={iconOnly ? "sr-only" : "text-xs text-muted"}>{status}</span>
  </span>
}

export function EventLogTools({ events, graph, runId }: { events: BuilderEvent[]; graph: string; runId?: string }) {
  return <div className="mb-3 flex flex-wrap items-center gap-2">
    <CopyButton value={events} label="Copy ALL" />
    <button type="button" className={button} aria-label="Export log" title="Export log" onClick={() => {
      const blob = new Blob([JSON.stringify(events, null, 2) + "\n"], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = `${graph}-${runId ?? "run"}-events.json`.replace(/[^a-zA-Z0-9._-]/g, "_")
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    }}><Icon name="download" size={16} /></button>
    <span className="text-xs text-muted">{events.length} events · JSON · oldest first</span>
  </div>
}

export function EventWithCopy({ event, children }: { event: BuilderEvent; children: ReactNode }) {
  return <div className="event-card relative min-w-0">
    {children}
    <div className="absolute right-1 top-1"><CopyButton value={event} label="Copy event" iconOnly /></div>
  </div>
}
