import { useState, type ReactNode } from "react"
import type { BuilderEvent } from "@/api"

const button = "rounded border border-outline-variant px-2 py-1 text-xs hover:bg-surface-high disabled:opacity-40"

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

function CopyButton({ value, label }: { value: unknown; label: string }) {
  const [status, setStatus] = useState("")
  return <span className="inline-flex items-center gap-2">
    <button type="button" className={button} aria-label={label} onClick={async () => {
      try {
        await copyJson(value)
        setStatus("Copied")
      } catch {
        setStatus("Copy failed; check clipboard permission")
      }
    }}>{label}</button>
    <span role="status" className="text-xs text-muted">{status}</span>
  </span>
}

export function EventLogTools({ events, graph, runId }: { events: BuilderEvent[]; graph: string; runId?: string }) {
  return <div className="mb-3 flex flex-wrap items-center gap-2">
    <CopyButton value={events} label="Copy whole log" />
    <button type="button" className={button} onClick={() => {
      const blob = new Blob([JSON.stringify(events, null, 2) + "\n"], { type: "application/json" })
      const url = URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = url
      link.download = `${graph}-${runId ?? "run"}-events.json`.replace(/[^a-zA-Z0-9._-]/g, "_")
      document.body.append(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 0)
    }}>Export log</button>
    <span className="text-xs text-muted">{events.length} events · JSON · oldest first</span>
  </div>
}

export function EventWithCopy({ event, children }: { event: BuilderEvent; children: ReactNode }) {
  return <div className="space-y-1">
    <div className="flex justify-end"><CopyButton value={event} label="Copy event" /></div>
    {children}
  </div>
}
