import { useEffect, useRef, useState } from "react"
import type { AiModel } from "../ai"
import { Icon } from "./ui"

interface Props {
  provider: string
  model: string
  models: AiModel[]
  /** A turn is running; the server rejects switches until it ends. */
  locked: boolean
  switching: boolean
  onSelect: (model: string) => void
}

/** The connection badge doubles as the model switcher: click, filter, pick. */
export function AiModelPicker({ provider, model, models, locked, switching, onSelect }: Props) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState("")
  const [active, setActive] = useState(0)
  const [upward, setUpward] = useState(true)
  const root = useRef<HTMLDivElement | null>(null)
  const button = useRef<HTMLButtonElement | null>(null)
  const list = useRef<HTMLUListElement | null>(null)
  const options = models.length ? models : [{ id: model, label: model }]
  const query = filter.trim().toLowerCase()
  const shown = options.filter(item => !query || item.id.toLowerCase().includes(query) || item.label.toLowerCase().includes(query))
  const disabled = locked || switching

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    document.addEventListener("pointerdown", onPointer)
    return () => document.removeEventListener("pointerdown", onPointer)
  }, [open])
  useEffect(() => { if (disabled) setOpen(false) }, [disabled])
  useEffect(() => { list.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" }) }, [active, open])

  const toggle = () => {
    if (open) { setOpen(false); return }
    // Open toward the larger free space: upward when the panel is docked low, downward when expanded.
    const rect = button.current?.getBoundingClientRect()
    setUpward(!rect || rect.top > window.innerHeight - rect.bottom)
    setFilter("")
    setActive(Math.max(0, options.findIndex(item => item.id === model)))
    setOpen(true)
  }
  const choose = (id: string) => {
    setOpen(false)
    button.current?.focus()
    if (id !== model) onSelect(id)
  }

  return <div ref={root} className="relative min-w-0 max-w-72 shrink">
    <button ref={button} type="button" disabled={disabled} aria-haspopup="listbox" aria-expanded={open}
      aria-label={`Model: ${model}. Change model`}
      title={locked ? "Model can be changed when the current turn finishes" : `${provider} · ${model} — click to change model`}
      className="mono inline-flex w-full min-w-0 items-center gap-1.5 rounded border border-primary-soft/20 bg-primary-soft/10 px-2 py-1 text-[10px] font-medium uppercase text-primary-soft hover:border-primary-soft/50 hover:bg-primary-soft/15 disabled:cursor-not-allowed disabled:opacity-60"
      onClick={toggle}>
      <span className={`h-1.5 w-1.5 shrink-0 rounded-full bg-primary-soft ${switching ? "animate-pulse" : ""}`} />
      <span className="truncate">{provider} · {model}</span>
      <Icon name={switching ? "progress_activity" : "expand_more"} size={14} className={`shrink-0 ${switching ? "animate-spin" : ""}`} />
    </button>
    {open && <div className={`absolute left-0 z-50 flex w-80 max-w-[80vw] flex-col overflow-hidden rounded-lg border border-outline-strong/60 bg-surface-container shadow-2xl ${upward ? "bottom-full mb-1" : "top-full mt-1"}`}>
      <div className="flex items-center gap-2 border-b border-outline-variant px-2 py-1.5">
        <Icon name="search" size={14} className="shrink-0 text-muted" />
        <input autoFocus aria-label="Filter models" placeholder={`Filter ${options.length} models…`} value={filter}
          className="min-w-0 flex-1 bg-transparent text-xs text-on-surface placeholder:text-faint focus:outline-none"
          onChange={e => { setFilter(e.target.value); setActive(0) }}
          onKeyDown={e => {
            if (e.key === "Escape") { e.preventDefault(); setOpen(false); button.current?.focus() }
            else if (e.key === "ArrowDown") { e.preventDefault(); setActive(i => Math.min(shown.length - 1, i + 1)) }
            else if (e.key === "ArrowUp") { e.preventDefault(); setActive(i => Math.max(0, i - 1)) }
            else if (e.key === "Enter" && shown[active]) { e.preventDefault(); choose(shown[active].id) }
          }} />
      </div>
      <ul ref={list} role="listbox" aria-label="Models" className="max-h-72 overflow-auto py-1">
        {shown.map((item, index) => <li key={item.id} role="option" aria-selected={item.id === model} data-index={index}
          className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-xs ${index === active ? "bg-primary-soft/15 text-on-surface" : "text-on-surface-variant"}`}
          onPointerEnter={() => setActive(index)} onClick={() => choose(item.id)}>
          <Icon name="check" size={14} className={`shrink-0 text-primary-soft ${item.id === model ? "" : "invisible"}`} />
          <span className="min-w-0 flex-1 truncate">{item.label}</span>
          {item.label !== item.id && <span className="mono shrink-0 truncate text-[10px] text-faint">{item.id}</span>}
        </li>)}
        {!shown.length && <li className="px-3 py-2 text-xs text-muted">No models match “{filter}”.</li>}
      </ul>
      <p className="border-t border-outline-variant px-3 py-1.5 text-[10px] text-faint">Applies from your next message · conversation is kept</p>
    </div>}
  </div>
}
