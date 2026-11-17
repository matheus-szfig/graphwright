// Session memory, never browser storage: drafts and test inputs may contain secrets.
// Panels can unmount freely; their data is keyed by graph/node/action instead of DOM.
import { useCallback, useSyncExternalStore } from "react"
import type { Dispatch, SetStateAction } from "react"

const values = new Map<string, unknown>()
const listeners = new Set<() => void>()
let revision = 0
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
const notify = () => { revision++; listeners.forEach((listener) => listener()) }
export const workspaceKey = (...parts: (string | number | undefined)[]) => JSON.stringify(parts)

export function clearAiWorkspace() {
  for (const key of values.keys()) {
    const [kind] = JSON.parse(key)
    if (kind === "ai-messages" || kind === "ai-turns" || kind === "ai-queue" || kind === "ai-proposals") values.set(key, [])
    if (kind === "ai-prompt") values.set(key, "")
    if (kind === "ai-inputs" || kind === "ai-reviews") values.set(key, {})
  }
  notify()
}

export function useWorkspaceState<T>(key: string, initial: T | (() => T)): [T, Dispatch<SetStateAction<T>>] {
  if (!values.has(key)) values.set(key, typeof initial === "function" ? (initial as () => T)() : initial)
  const snapshot = useCallback(() => values.get(key) as T, [key])
  const value = useSyncExternalStore(subscribe, snapshot)
  const setValue = useCallback<Dispatch<SetStateAction<T>>>((next) => {
    const old = values.get(key) as T
    const value = typeof next === "function" ? (next as (old: T) => T)(old) : next
    if (!Object.is(value, old)) { values.set(key, value); notify() }
  }, [key])
  return [value, setValue]
}

interface Draft { text: string; base: string | null; saved: string | null }
export function useDraft(graph: string, tab: string, identity: string, initial: string | null) {
  const key = workspaceKey("draft", graph, tab, identity)
  const [draft, setDraft] = useWorkspaceState<Draft>(key, () => ({ text: initial ?? "", base: initial, saved: initial }))
  const receive = useCallback((saved: string) => setDraft((old) => {
    if (old.saved === saved) return old
    return old.base === null || old.text === old.base || old.text === saved
      ? { text: saved, base: saved, saved } : { ...old, saved }
  }), [setDraft])
  const edit = (text: string) => setDraft((old) => ({ ...old, text }))
  const commit = (submitted: string, saved = submitted) => setDraft((old) => ({
    text: old.text === submitted ? saved : old.text, base: saved, saved,
  }))
  const discard = () => setDraft((old) => ({ text: old.saved ?? "", base: old.saved, saved: old.saved }))
  return { ...draft, key, edit, receive, commit, discard, dirty: draft.base !== null && draft.text !== draft.base,
    conflict: draft.base !== null && draft.text !== draft.base && draft.base !== draft.saved }
}

export function useWip(graph: string): Set<string> {
  useSyncExternalStore(subscribe, () => revision)
  const tabs = new Set<string>()
  for (const [key, value] of values) {
    const [kind, name, tab] = JSON.parse(key)
    const draft = value as Draft
    if (kind === "draft" && name === graph && draft.base !== null && draft.text !== draft.base) tabs.add(tab)
  }
  return tabs
}
