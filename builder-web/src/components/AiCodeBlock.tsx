import { useEffect, useState } from "react"
import { Icon } from "./ui"

function highlight(line: string) {
  return line.split(/("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:true|false|null|undefined)\b|\b\d+(?:\.\d+)?\b|\b[\w.-]+(?=:\s|:$))/g)
    .map((token, i) => <span key={i} className={/^["']/.test(token) ? "text-secondary-soft" : /^(true|false|null|undefined)$/.test(token)
      ? "text-tertiary-soft" : /^\d/.test(token) ? "text-warning" : /^[\w.-]+$/.test(token) ? "text-primary-soft" : undefined}>{token}</span>)
}

export function AiCodeBlock({ text, language = "text", label, maxHeight = "max-h-80", wrap = false }: {
  text: string; language?: string; label?: string; maxHeight?: string; wrap?: boolean
}) {
  const [copied, setCopied] = useState("")
  useEffect(() => setCopied(""), [text])
  const copy = async () => {
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text)
      else {
        const previous = document.activeElement as HTMLElement | null
        const field = document.createElement("textarea")
        field.value = text
        field.style.position = "fixed"
        field.style.opacity = "0"
        document.body.append(field)
        try { field.select(); if (!document.execCommand("copy")) throw new Error("Clipboard unavailable") }
        finally { field.remove(); previous?.focus() }
      }
      setCopied("Copied")
    } catch { setCopied("Copy failed. Check clipboard permission.") }
  }
  return <div className="min-w-0 overflow-hidden rounded-lg border border-outline-variant bg-recess">
    <div className="flex items-center gap-2 border-b border-outline-variant bg-surface-container/60 px-3 py-2">
      <Icon name={language === "diff" ? "difference" : "code"} size={14} className="text-secondary-soft" />
      <span className="mono min-w-0 flex-1 truncate text-[10px] uppercase tracking-wide text-muted">{label || language}</span>
      <button type="button" className="flex items-center gap-1 rounded px-1 text-[10px] text-muted hover:text-on-surface focus-visible:outline focus-visible:outline-primary"
        aria-label={`Copy ${label || language}`} title={copied || `Copy ${label || language}`} onClick={() => void copy()}>
        <Icon name={copied === "Copied" ? "check" : "content_copy"} size={14} />{copied === "Copied" ? "Copied" : "Copy"}
      </button>
      <span role="status" className="sr-only">{copied}</span>
    </div>
    <pre className={`mono ${maxHeight} ${wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"} overflow-auto py-2 text-[11px] leading-5 text-on-surface-variant`}><code>
      {text.split("\n").map((line, i) => {
        const diff = language === "diff"
        const tone = diff && line.startsWith("+") && !line.startsWith("+++") ? "bg-success/10 text-success"
          : diff && line.startsWith("-") && !line.startsWith("---") ? "bg-danger/10 text-danger-soft"
          : diff && /^(\+\+\+|---|@@)/.test(line) ? "text-primary-soft" : ""
        return <span key={i} className={`block ${wrap ? "w-full" : "min-w-full w-max"} px-3 ${tone}`}>
          {diff || language === "text" ? line || " " : highlight(line || " ")}{"\n"}
        </span>
      })}
    </code></pre>
  </div>
}
