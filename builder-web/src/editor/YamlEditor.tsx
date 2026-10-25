import { useEffect, useRef } from "react"
import { acceptCompletion, autocompletion, completionKeymap, startCompletion } from "@codemirror/autocomplete"
import { defaultKeymap, history, historyKeymap, indentWithTab } from "@codemirror/commands"
import { yaml } from "@codemirror/lang-yaml"
import { bracketMatching, indentOnInput } from "@codemirror/language"
import { EditorState } from "@codemirror/state"
import { oneDark } from "@codemirror/theme-one-dark"
import { EditorView, highlightActiveLine, keymap, lineNumbers } from "@codemirror/view"

import { yamlCompletionSource, type EditorContext } from "./completions"

export interface YamlEditorProps {
  value: string
  onChange: (value: string) => void
  context: EditorContext
  className?: string
}

/** CodeMirror YAML editor with Tab-to-accept completions. The completion source reads
 * the latest `context` through a ref, so page observations and graph changes flow in
 * without rebuilding the editor. */
export function YamlEditor({ value, onChange, context, className }: YamlEditorProps) {
  const host = useRef<HTMLDivElement>(null)
  const view = useRef<EditorView | null>(null)
  const ctxRef = useRef(context)
  const onChangeRef = useRef(onChange)
  ctxRef.current = context
  onChangeRef.current = onChange
  // The completion source is re-created every render and reached through a ref, so a
  // hot-reloaded completions module takes effect without rebuilding (or reloading) the
  // editor — the EditorView and its extensions are only built once below.
  const sourceRef = useRef(yamlCompletionSource(() => ctxRef.current))
  sourceRef.current = yamlCompletionSource(() => ctxRef.current)
  // True while we replace the document from the `value` prop, so that programmatic
  // loads (opening a node, reload after save) are not reported as user edits.
  const syncing = useRef(false)

  useEffect(() => {
    if (!host.current) return
    const state = EditorState.create({
      doc: value,
      extensions: [
        lineNumbers(),
        highlightActiveLine(),
        history(),
        bracketMatching(),
        indentOnInput(),
        yaml(),
        oneDark,
        autocompletion({
          override: [(cx) => sourceRef.current(cx)],
          activateOnTyping: true,
          icons: false,
          defaultKeymap: true,
        }),
        keymap.of([
          { key: "Tab", run: acceptCompletion },
          { key: "Ctrl-Space", run: startCompletion },
          indentWithTab,
          ...completionKeymap,
          ...historyKeymap,
          ...defaultKeymap,
        ]),
        EditorState.tabSize.of(2),
        EditorView.updateListener.of((u) => {
          if (u.docChanged && !syncing.current) onChangeRef.current(u.state.doc.toString())
        }),
        EditorView.theme({
          "&": { height: "100%", fontSize: "12px" },
          ".cm-scroller": { fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace", lineHeight: "1.5" },
          ".cm-tooltip.cm-tooltip-autocomplete > ul": { fontFamily: "ui-monospace, Menlo, Consolas, monospace", fontSize: "12px", maxHeight: "260px" },
          ".cm-completionDetail": { opacity: "0.6", marginLeft: "8px" },
        }),
      ],
    })
    const v = new EditorView({ state, parent: host.current })
    view.current = v
    return () => {
      v.destroy()
      view.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // External value changes (switching node, reload after save) replace the document.
  useEffect(() => {
    const v = view.current
    if (!v) return
    const current = v.state.doc.toString()
    if (current !== value) {
      syncing.current = true
      try {
        v.dispatch({ changes: { from: 0, to: current.length, insert: value } })
      } finally {
        syncing.current = false
      }
    }
  }, [value])

  return <div ref={host} className={`min-h-0 overflow-hidden rounded border border-zinc-700 ${className ?? ""}`} />
}
