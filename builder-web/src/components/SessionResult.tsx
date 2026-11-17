import { useState } from "react"
import { Icon } from "./ui"
import { AiCodeBlock } from "./AiCodeBlock"

export function SessionResult({ result }: { result: unknown }) {
  const [open, setOpen] = useState(false)
  return <div className="hud pointer-events-auto absolute inset-x-4 bottom-4 mx-auto max-h-[45%] max-w-xl overflow-auto border-success/40 p-3 text-xs" aria-label="Run result">
    <button type="button" className="flex w-full items-center gap-2 text-success" aria-expanded={open} onClick={() => setOpen(!open)}>
      <Icon name="check_circle" size={16} /><span>Run completed</span>
      <span className="ml-auto">{open ? "Hide result" : "View result"}</span><Icon name={open ? "expand_more" : "chevron_right"} size={16} />
    </button>
    {open && <div className="mt-2 min-w-0"><AiCodeBlock text={JSON.stringify(result, null, 2)} language="json" label="Run result" maxHeight="max-h-48" wrap /></div>}
  </div>
}
