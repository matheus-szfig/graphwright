import { useEffect, useRef } from "react"
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands"
import { javascript } from "@codemirror/lang-javascript"
import { bracketMatching, indentOnInput } from "@codemirror/language"
import { EditorState } from "@codemirror/state"
import { oneDark } from "@codemirror/theme-one-dark"
import { EditorView, keymap, lineNumbers } from "@codemirror/view"
import { editorState, rememberEditor, restoreScroll } from "./memory"

export interface CodeEditorProps {
  memoryKey?: string
  value: string
  onChange?: (value: string) => void
  /** Ctrl/Cmd+Enter — the console's "run". */
  onSubmit?: () => void
  readOnly?: boolean
  className?: string
}

/** CodeMirror for JavaScript (code-node bodies, in-page scripts, the console). Built
 * once; `value` changes from outside replace the document without counting as edits. */
export function CodeEditor({ value, onChange, onSubmit, readOnly = false, className, memoryKey }: CodeEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const onChangeRef = useRef(onChange)
  const onSubmitRef = useRef(onSubmit)
  onChangeRef.current = onChange
  onSubmitRef.current = onSubmit
  const syncing = useRef(false)

  useEffect(() => {
    if (!host.current) return
    const state = editorState(memoryKey, {
      doc: value,
      extensions: [
        lineNumbers(),
        history(),
        bracketMatching(),
        indentOnInput(),
        javascript(),
        oneDark,
        EditorState.readOnly.of(readOnly),
        EditorView.editable.of(!readOnly),
        keymap.of([
          { key: "Mod-Enter", run: () => (onSubmitRef.current?.(), true) },
          indentWithTab,
          ...defaultKeymap,
          ...historyKeymap,
        ]),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !syncing.current) onChangeRef.current?.(u.state.doc.toString())
        }),
        EditorView.theme({ "&": { fontSize: "12px" }, ".cm-content": { fontFamily: "'JetBrains Mono', monospace" } }),
      ],
    })
    view.current = new EditorView({ state, parent: host.current })
    restoreScroll(memoryKey, view.current)
    return () => {
      if (view.current) rememberEditor(memoryKey, view.current)
      view.current?.destroy()
      view.current = null
    }
    // Built once; `value` is synced below and readOnly is fixed per instance.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const v = view.current
    if (!v || v.state.doc.toString() === value) return
    syncing.current = true
    v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } })
    syncing.current = false
  }, [value])

  return <div ref={host} className={`overflow-hidden rounded border border-outline-variant ${className ?? ""}`} />
}
