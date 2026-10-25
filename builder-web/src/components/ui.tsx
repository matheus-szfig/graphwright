import type { ReactNode } from "react"

// Shared look for every panel, following the Synthetic Flow design tokens in index.css.
export const cls = {
  input: "mono min-w-0 rounded border border-outline-variant bg-recess px-2 py-1 text-xs text-on-surface placeholder:text-faint focus:border-primary focus:outline-none disabled:opacity-60",
  select: "min-w-0 rounded border border-outline-variant bg-recess px-1.5 py-1 text-xs text-on-surface focus:border-primary focus:outline-none",
  btn: "inline-flex items-center justify-center gap-1 rounded border border-outline-variant bg-surface px-2 py-1 text-xs text-on-surface-variant hover:bg-surface-high hover:text-on-surface disabled:opacity-40 disabled:hover:bg-surface",
  primary: "inline-flex items-center justify-center gap-1 rounded bg-primary px-2.5 py-1 text-xs font-medium text-on-primary hover:brightness-110 disabled:opacity-40 disabled:hover:brightness-100",
  success: "inline-flex items-center justify-center gap-1 rounded bg-success-deep px-3 py-1.5 text-xs font-medium text-success hover:brightness-125 disabled:opacity-40",
  danger: "inline-flex items-center justify-center gap-1 rounded border border-danger/40 px-2 py-1 text-xs text-danger-soft hover:bg-danger/10 disabled:opacity-40",
  ghost: "rounded px-1.5 py-0.5 text-xs text-muted hover:bg-surface-high hover:text-on-surface",
  label: "label-caps text-faint",
  muted: "text-xs text-muted",
}

/** Themed checkbox: recessed box, primary fill when checked, label-caps text. */
export function Check({ checked, onChange, label, title }: { checked: boolean; onChange: (v: boolean) => void; label: string; title?: string }) {
  return (
    <label className="inline-flex cursor-pointer select-none items-center gap-1.5" title={title}>
      <input type="checkbox" className="peer sr-only" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded border border-outline-strong bg-recess text-on-primary transition-colors peer-checked:border-primary peer-checked:bg-primary peer-focus-visible:ring-1 peer-focus-visible:ring-primary-soft">
        {checked && <Icon name="check" size={13} className="font-bold" />}
      </span>
      {/* line-height matches the 16px box so the caps sit on its vertical centre */}
      <span className="label-caps text-on-surface-variant" style={{ lineHeight: "16px" }}>
        {label}
      </span>
    </label>
  )
}

/** Material Symbols Outlined glyph. */
export function Icon({ name, size = 18, fill = false, className = "" }: { name: string; size?: number; fill?: boolean; className?: string }) {
  return (
    <span className={`msi ${fill ? "fill" : ""} ${className}`} style={{ fontSize: size }} aria-hidden>
      {name}
    </span>
  )
}

export type Tone = "zinc" | "primary" | "secondary" | "tertiary" | "success" | "warning" | "danger"

const TONES: Record<Tone, string> = {
  zinc: "bg-surface-highest text-on-surface-variant",
  primary: "bg-primary/30 text-primary-soft",
  secondary: "bg-secondary/20 text-secondary-soft",
  tertiary: "bg-tertiary/45 text-[#f3e8ff]",
  success: "bg-success/20 text-[#6ee7b7]",
  warning: "bg-warning/25 text-[#fcd34d]",
  danger: "bg-danger/25 text-danger-soft",
}

export function Badge({ children, tone = "zinc", title, dot }: { children: ReactNode; tone?: Tone; title?: string; dot?: boolean }) {
  return (
    <span className={`label-caps inline-flex items-center gap-1 rounded px-1.5 py-0.5 ${TONES[tone]}`} title={title}>
      {dot && <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </span>
  )
}

/** Mono key: value chip, as on the design's node cards. */
export function Chip({ k, v, tone = "secondary" }: { k: string; v: ReactNode; tone?: Tone }) {
  const vColor = { zinc: "text-on-surface-variant", primary: "text-primary-soft", secondary: "text-secondary-soft", tertiary: "text-tertiary-soft", success: "text-success", warning: "text-warning", danger: "text-danger-soft" }[tone]
  return (
    <span className="mono inline-flex max-w-full items-center gap-1 rounded border border-outline-variant bg-recess px-1.5 py-0.5 text-[11px]">
      <span className="text-faint">{k}:</span>
      <span className={`truncate ${vColor}`}>{v}</span>
    </span>
  )
}

export interface SectionProps {
  title: string
  icon?: string
  collapsed: boolean
  onToggle: () => void
  summary?: ReactNode
  actions?: ReactNode
  children: ReactNode
}

/** A docked-rail block: label-caps header with a chevron, bordered body. */
export function Section({ title, icon, collapsed, onToggle, summary, actions, children }: SectionProps) {
  return (
    <section className="mx-3 overflow-hidden rounded-md border border-outline-variant bg-surface-container">
      <div className={`flex items-center gap-2 px-3 py-2 ${collapsed ? "" : "border-b border-outline-variant"}`}>
        <button className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={onToggle} title={collapsed ? `expand ${title}` : `collapse ${title}`}>
          <Icon name={collapsed ? "chevron_right" : "expand_more"} size={16} className="shrink-0 text-faint" />
          {icon && <Icon name={icon} size={16} className="shrink-0 text-primary-soft" />}
          <span className="label-caps text-on-surface">{title}</span>
          {summary && collapsed && <span className="mono min-w-0 truncate text-[11px] text-muted">{summary}</span>}
        </button>
        {actions && <div className="flex shrink-0 items-center gap-1" onMouseDown={(e) => e.stopPropagation()}>{actions}</div>}
      </div>
      {!collapsed && <div className="space-y-3 px-3 py-3">{children}</div>}
    </section>
  )
}

export function readFlag(key: string, fallback = false): boolean {
  try {
    const v = localStorage.getItem(key)
    return v === null ? fallback : v === "1"
  } catch {
    return fallback
  }
}

export function writeFlag(key: string, value: boolean): void {
  try {
    localStorage.setItem(key, value ? "1" : "0")
  } catch {
    /* private mode etc. */
  }
}
