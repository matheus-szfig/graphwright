import { historyField } from "@codemirror/commands"
import { EditorState, type EditorStateConfig } from "@codemirror/state"
import type { EditorView } from "@codemirror/view"

// Retain selection, undo history and scroll without retaining DOM or old listeners.
const snapshots = new Map<string, { state: ReturnType<EditorState["toJSON"]>; top: number; left: number }>()

export function editorState(key: string | undefined, config: EditorStateConfig): EditorState {
  const saved = key ? snapshots.get(key) : undefined
  return saved && saved.state.doc === config.doc
    ? EditorState.fromJSON(saved.state, config, { history: historyField })
    : EditorState.create(config)
}

export function restoreScroll(key: string | undefined, view: EditorView): void {
  const saved = key ? snapshots.get(key) : undefined
  if (saved) { view.scrollDOM.scrollTop = saved.top; view.scrollDOM.scrollLeft = saved.left }
}

export function rememberEditor(key: string | undefined, view: EditorView): void {
  if (key) snapshots.set(key, { state: view.state.toJSON({ history: historyField }),
    top: view.scrollDOM.scrollTop, left: view.scrollDOM.scrollLeft })
}
