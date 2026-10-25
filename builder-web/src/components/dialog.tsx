import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react"

import { Icon, cls } from "./ui"

// In-theme replacements for window.alert / confirm / prompt. One dialog at a time;
// a second request waits in the queue. Each call returns a promise:
//   alert   → void       confirm → boolean       prompt → string | null (null = cancelled)

type Tone = "primary" | "danger"

interface BaseOptions {
  title: string
  message?: ReactNode
  tone?: Tone
  icon?: string
}
export interface AlertOptions extends BaseOptions { okLabel?: string }
export interface ConfirmOptions extends BaseOptions { confirmLabel?: string; cancelLabel?: string }
export interface PromptOptions extends ConfirmOptions {
  label?: string
  defaultValue?: string
  placeholder?: string
  /** Live check; return an error message to block submit, or null when the value is fine. */
  validate?: (value: string) => string | null
  /** Applied to every keystroke, e.g. forcing snake_case. */
  transform?: (value: string) => string
}

type Pending =
  | { kind: "alert"; options: AlertOptions; resolve: (v: void) => void }
  | { kind: "confirm"; options: ConfirmOptions; resolve: (v: boolean) => void }
  | { kind: "prompt"; options: PromptOptions; resolve: (v: string | null) => void }
type Request = Pending & { id: number }

export interface DialogApi {
  alert: (options: AlertOptions) => Promise<void>
  confirm: (options: ConfirmOptions) => Promise<boolean>
  prompt: (options: PromptOptions) => Promise<string | null>
}

const DialogContext = createContext<DialogApi | null>(null)

export function useDialog(): DialogApi {
  const ctx = useContext(DialogContext)
  if (!ctx) throw new Error("useDialog needs a <DialogProvider> above it")
  return ctx
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [queue, setQueue] = useState<Request[]>([])
  const nextId = useRef(0)
  const push = useCallback((r: Pending) => setQueue((q) => [...q, { ...r, id: nextId.current++ }]), [])

  const api = useMemo<DialogApi>(
    () => ({
      alert: (options) => new Promise((resolve) => push({ kind: "alert", options, resolve })),
      confirm: (options) => new Promise((resolve) => push({ kind: "confirm", options, resolve })),
      prompt: (options) => new Promise((resolve) => push({ kind: "prompt", options, resolve })),
    }),
    [push],
  )

  const current = queue[0]
  const settle = (value: boolean | string | null) => {
    if (!current) return
    if (current.kind === "alert") current.resolve()
    else if (current.kind === "confirm") current.resolve(value === true)
    else current.resolve(typeof value === "string" ? value : null)
    setQueue((q) => q.slice(1))
  }

  return (
    <DialogContext.Provider value={api}>
      {children}
      {/* key: a fresh component (and fresh input state) per request */}
      {current && <Dialog key={current.id} request={current} onSettle={settle} />}
    </DialogContext.Provider>
  )
}

function Dialog({ request, onSettle }: { request: Request; onSettle: (value: boolean | string | null) => void }) {
  const { options, kind } = request
  const prompt = kind === "prompt" ? (options as PromptOptions) : null
  const [value, setValue] = useState(prompt?.defaultValue ?? "")
  const error = prompt?.validate?.(value) ?? null
  const tone = options.tone ?? "primary"
  const inputRef = useRef<HTMLInputElement>(null)
  const okRef = useRef<HTMLButtonElement>(null)

  const cancel = () => onSettle(kind === "prompt" ? null : false)
  const ok = () => {
    if (kind === "prompt") {
      if (!error) onSettle(value)
    } else onSettle(true)
  }

  // Focus the field (text selected) or the primary button; give focus back on close.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null
    if (inputRef.current) {
      inputRef.current.focus()
      inputRef.current.select()
    } else okRef.current?.focus()
    return () => before?.focus?.()
  }, [])

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault()
      e.stopPropagation()
      if (kind === "alert") onSettle(true)
      else cancel()
    } else if (e.key === "Enter" && kind === "prompt") {
      e.preventDefault()
      ok()
    }
  }

  const okLabel =
    kind === "alert" ? ((options as AlertOptions).okLabel ?? "OK") : ((options as ConfirmOptions).confirmLabel ?? (kind === "prompt" ? "OK" : "Confirm"))
  const cancelLabel = (options as ConfirmOptions).cancelLabel ?? "Cancel"
  const icon = options.icon ?? (tone === "danger" ? "warning" : kind === "prompt" ? "edit" : "info")

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 px-4"
      onMouseDown={(e) => e.target === e.currentTarget && kind !== "alert" && cancel()}
      onKeyDown={onKeyDown}
    >
      <div role={kind === "alert" ? "alertdialog" : "dialog"} aria-modal="true" aria-labelledby="dialog-title" className="hud w-full max-w-md p-4">
        <div className="flex items-start gap-3">
          <span className={`mt-0.5 shrink-0 ${tone === "danger" ? "text-danger-soft" : "text-primary-soft"}`}>
            <Icon name={icon} size={20} />
          </span>
          <div className="min-w-0 flex-1 space-y-2">
            <h2 id="dialog-title" className="text-sm font-medium text-on-surface">
              {options.title}
            </h2>
            {options.message && <div className="text-xs leading-relaxed text-on-surface-variant">{options.message}</div>}
            {prompt && (
              <label className="block space-y-1">
                {prompt.label && <span className={cls.label}>{prompt.label}</span>}
                <input
                  ref={inputRef}
                  className={`${cls.input} w-full ${error ? "border-danger" : ""}`}
                  value={value}
                  placeholder={prompt.placeholder}
                  onChange={(e) => setValue(prompt.transform ? prompt.transform(e.target.value) : e.target.value)}
                  aria-invalid={!!error}
                />
                {error && <span className="block text-xs text-danger-soft">{error}</span>}
              </label>
            )}
          </div>
        </div>
        <div className="mt-4 flex justify-end gap-1.5">
          {kind !== "alert" && (
            <button className={cls.btn} onClick={cancel}>
              {cancelLabel}
            </button>
          )}
          <button ref={okRef} className={tone === "danger" ? cls.danger : cls.primary} disabled={!!error} onClick={ok}>
            {okLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
