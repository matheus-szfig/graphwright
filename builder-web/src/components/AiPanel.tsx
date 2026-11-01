import { useEffect, useRef, useState } from "react"
import { ai, appendAiEvent, type AiConnection, type AiProposal } from "../ai"
import type { GraphView } from "../api"
import { clearAiWorkspace, useWip, useWorkspaceState, workspaceKey } from "../workspace"
import { Icon, cls } from "./ui"
import { TurnView, duration, toolLabel, type AiTurn, type TurnStatus } from "./AiTimeline"
import { AiProposalCard, type ProposalResolution } from "./AiProposalCard"
import { AiCodeBlock } from "./AiCodeBlock"
import { AiModelPicker } from "./AiModelPicker"

const QUICK_PROMPTS = [
  { label: "Explain this graph", text: "Explain this graph, its main steps, transitions, and outcomes." },
  { label: "Extract rows to JSON", text: "Help me extract the visible table rows into a consistent JSON structure using PageState. Do not navigate or expand other pages." },
  { label: "Add conditional rule", text: "Help me add a conditional transition to this graph. Ask which condition and destination to use if they are not clear." },
  { label: "Format date & currency", text: "Help me normalize extracted dates and currency values. Propose a transformation and test it with synthetic examples." },
  { label: "Validate output", text: "Help me validate the extracted output: required fields, expected types, and nonempty arrays. Use synthetic test data." },
]

interface Props {
  view: GraphView
  selected?: string
  sessionId?: string
  values: Record<string, string>
  privateValues: string[]
  onGraphChanged: (view: GraphView) => void
  onSessionChanged: (id: string) => Promise<void>
}

export function AiPanel(p: Props) {
  const [open, setOpen] = useState(false)
  const [height, setHeight] = useState(460)
  const [expanded, setExpanded] = useState(false)
  const [viewportHeight, setViewportHeight] = useState(() => window.innerHeight)
  const [viewportWidth, setViewportWidth] = useState(() => window.innerWidth)
  const [contextWidth, setContextWidth] = useState(420)
  const contextResize = useRef<{ x: number; width: number } | null>(null)
  const contextMaxWidth = Math.min(800, viewportWidth * 0.7)
  const contextMinWidth = Math.min(260, contextMaxWidth)
  const contextPanelWidth = Math.max(contextMinWidth, Math.min(contextWidth, contextMaxWidth))
  const resize = useRef<{ y: number; height: number } | null>(null)
  const maxHeight = Math.max(80, Math.min(viewportHeight * 0.9, viewportHeight - 80))
  const minHeight = Math.min(260, maxHeight)
  const panelHeight = expanded ? maxHeight : Math.max(minHeight, Math.min(height, maxHeight))
  const resizeTo = (next: number) => {
    setExpanded(false)
    setHeight(Math.max(minHeight, Math.min(next, maxHeight)))
  }
  const [connection, setConnection] = useWorkspaceState<AiConnection | null>(workspaceKey("ai-connection"), null)
  const [turns, setTurns] = useWorkspaceState<AiTurn[]>(workspaceKey("ai-turns", p.view.name), [])
  const [queued, setQueued] = useWorkspaceState<string[]>(workspaceKey("ai-queue", p.view.name), [])
  const [proposals, setProposals] = useWorkspaceState<AiProposal[]>(workspaceKey("ai-proposals", p.view.name), [])
  const [resolutions, setResolutions] = useWorkspaceState<Record<string, ProposalResolution>>(workspaceKey("ai-reviews", p.view.name), {})
  const [prompt, setPrompt] = useWorkspaceState<string>(workspaceKey("ai-prompt", p.view.name), "")
  const [provider, setProvider] = useState("openai")
  const [triageModel, setTriageModel] = useState("")
  const [switchingModel, setSwitchingModel] = useState(false)
  const [key, setKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [activity, setActivity] = useState("")
  const [now, setNow] = useState(Date.now)
  const [following, setFollowing] = useState(true)
  const [error, setError] = useState("")
  const [context, setContext] = useState<unknown>(null)
  const [showContext, setShowContext] = useState(false)
  const [newInputs, setNewInputs] = useWorkspaceState<Record<string, Record<string, string>>>(workspaceKey("ai-inputs", p.view.name), {})
  const controller = useRef<AbortController | null>(null)
  const activeTurn = useRef<string | null>(null)
  const advanceQueue = useRef(false)
  const queuePaused = useRef(false)
  const stopRequested = useRef(false)
  const composer = useRef<HTMLTextAreaElement | null>(null)
  const lastSignal = useRef(Date.now())
  const mounted = useRef(true)
  const log = useRef<HTMLDivElement | null>(null)
  const wip = useWip(p.view.name)
  // Messages sent to the running turn that it has not read yet (FIFO, mirrored for render).
  const steeringRef = useRef<string[]>([])
  const [steering, setSteering] = useState<string[]>([])
  const setPending = (next: string[]) => { steeringRef.current = next; setSteering(next) }
  const cancelling = useRef<Promise<void> | null>(null)
  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      controller.current?.abort()
      setTurns(old => old.map(turn => turn.id === activeTurn.current && turn.status === "running"
        ? { ...turn, status: "stopped", finishedAt: Date.now() } : turn))
    }
  }, [])
  useEffect(() => { if (following) log.current?.scrollTo({ top: log.current.scrollHeight }) }, [turns, activity, following, open])
  useEffect(() => {
    if (!busy) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [busy])
  useEffect(() => {
    if (!composer.current) return
    composer.current.style.height = "auto"
    composer.current.style.height = `${Math.min(160, Math.max(60, composer.current.scrollHeight))}px`
  }, [prompt, open, connection])
  useEffect(() => {
    const onResize = () => { setViewportHeight(window.innerHeight); setViewportWidth(window.innerWidth) }
    window.addEventListener("resize", onResize)
    return () => window.removeEventListener("resize", onResize)
  }, [])

  const body = () => ({ graph: p.view.name, session_id: p.sessionId, selected: p.selected, values: p.values, private_values: p.privateValues })
  const perform = async (fn: () => Promise<void>, label = "Working…") => {
    setError(""); setBusy(true); setActivity(label)
    try { await fn() } catch (e) { if (mounted.current) setError((e as Error).message) }
    finally { if (mounted.current) { setBusy(false); setActivity("") } }
  }
  const runTurn = async (text: string) => {
    if (!connection || controller.current) return
    // Claim the slot before any await so a second send cannot start another turn.
    const abort = new AbortController()
    controller.current = abort
    setError(""); setBusy(true); setActivity("Preparing context…"); setFollowing(true)
    const id = crypto.randomUUID()
    const startedAt = Date.now()
    setNow(startedAt)
    lastSignal.current = startedAt
    activeTurn.current = id
    stopRequested.current = false
    setTurns(old => [...old, { id, message: text, status: "running", startedAt, events: [], model: connection.model }])
    const finish = (status: TurnStatus) => setTurns(old => old.map(turn => turn.id === id
      ? { ...turn, status, finishedAt: Date.now() } : turn))
    try {
      // Stop releases the chat at once; let the server finish cancelling the previous turn first.
      if (cancelling.current) await cancelling.current
      await ai.turn(connection.token, { ...body(), message: `${p.selected ? `Selected node: ${p.selected}\n` : ""}${text}` }, abort.signal, event => {
        if (!mounted.current) return
        lastSignal.current = Date.now()
        if (event.kind === "heartbeat") return
        setTurns(old => old.map(turn => turn.id === id ? { ...turn,
          message: event.kind === "context" ? event.message || text : turn.message,
          events: appendAiEvent(turn.events, event) } : turn))
        if (event.kind === "context") {
          setContext(event.context)
        }
        if (event.kind === "steer") { setPending(steeringRef.current.slice(1)); setActivity("Reading your message…") }
        if (event.kind === "activity") setActivity(event.text || "")
        if (event.kind === "text_delta") setActivity("Writing response…")
        if (event.kind === "tool_start") setActivity(toolLabel(event.name))
        if (event.kind === "tool_end") setActivity("Preparing the next step…")
        if (event.kind === "error") { finish("failed"); advanceQueue.current = false; queuePaused.current = true }
        if (event.kind === "cancelled") { finish("stopped"); advanceQueue.current = false; queuePaused.current = true }
        if (event.kind === "proposal" && event.proposal) {
          const proposal = event.proposal
          setProposals(old => [...old.filter(item => item.id !== proposal.id), proposal])
        }
        if (event.kind === "done") {
          finish(event.reason === "limit" ? "limited" : "completed")
          if (event.reason === "limit") queuePaused.current = true
          advanceQueue.current = !stopRequested.current && !queuePaused.current
        }
      })
      if (p.sessionId && mounted.current) void p.onSessionChanged(p.sessionId).catch(() => {
        if (mounted.current) setError("The AI turn finished, but the session status could not be refreshed.")
      })
    } catch (e) {
      advanceQueue.current = false
      queuePaused.current = true
      if (mounted.current) {
        finish(abort.signal.aborted ? "stopped" : "failed")
        setTurns(old => old.map(turn => turn.id === id ? { ...turn, events: [...turn.events, abort.signal.aborted
          ? { kind: "cancelled", text: "Stopped by you. Completed proposals are still available." }
          : { kind: "error", text: (e as Error).message }] } : turn))
      }
    } finally {
      if (controller.current === abort) controller.current = null
      if (activeTurn.current === id) activeTurn.current = null
      // Messages this turn never read become queued turns; a stop or failure keeps them paused.
      const unread = steeringRef.current
      if (unread.length) { setPending([]); setQueued(old => [...unread, ...old]) }
      if (mounted.current && !controller.current) { setBusy(false); setActivity("") }
    }
  }
  const steer = async (text: string) => {
    if (!connection) return
    setPending([...steeringRef.current, text])
    try { await ai.steer(connection.token, text) }
    catch {
      // The turn ended before it could read the message: send it as the next turn instead.
      const index = steeringRef.current.indexOf(text)
      if (index < 0) return
      setPending(steeringRef.current.filter((_, i) => i !== index))
      setQueued(old => [...old, text])
      if (!stopRequested.current) { advanceQueue.current = true; queuePaused.current = false }
    }
  }
  const send = () => {
    if (!connection || !prompt.trim() || (busy && !controller.current)) return
    const text = prompt.trim()
    setPrompt("")
    if (controller.current && !stopRequested.current) void steer(text)
    else if (controller.current) { setQueued(old => [...old, text]); advanceQueue.current = true; queuePaused.current = false }
    else void runTurn(text)
  }
  useEffect(() => {
    if (busy || !connection || !queued.length || !advanceQueue.current) return
    const [next, ...rest] = queued
    setQueued(rest)
    void runTurn(next)
  }, [busy, queued, connection])
  const stop = () => {
    const active = controller.current
    if (!connection || !active || stopRequested.current) return
    advanceQueue.current = false
    queuePaused.current = true
    stopRequested.current = true
    setActivity("Stopping…")
    const pending: Promise<void> = ai.cancel(connection.token).catch(e => {
      if (mounted.current) setError(`${(e as Error).message} The local stream was closed; server cancellation could not be confirmed.`)
    })
    cancelling.current = pending
    void pending.finally(() => { if (cancelling.current === pending) cancelling.current = null })
    // Like Esc in coding agents: the chat is usable immediately; the server cancels in the background.
    active.abort()
  }
  const currentTurn = turns.find(turn => turn.id === activeTurn.current)
  const lastTurn = turns[turns.length - 1]
  const lastUsage = lastTurn?.events.find(event => event.kind === "done")?.usage
  const proposalLocations = new Map<string, string>()
  turns.forEach(turn => turn.events.forEach((event, index) => {
    if (event.proposal) proposalLocations.set(event.proposal.id, `${turn.id}:${index}`)
  }))
  const insertPrompt = (text: string) => {
    setPrompt(old => old.trim() ? `${old}\n\n${text}` : text)
    composer.current?.focus()
  }
  const refreshContext = () => void perform(async () => { setContext(await ai.context(connection!.token, body())) }, "Preparing context preview…")
  const previewContext = () => {
    setShowContext(!showContext)
    if (!showContext && !busy) refreshContext()
  }
  const clearConversation = () => void perform(async () => {
    await ai.clear(connection!.token); clearAiWorkspace(); setContext(null); setShowContext(false); setNewInputs({}); advanceQueue.current = false; queuePaused.current = false
  })
  const switchModel = async (next: string) => {
    if (!connection || switchingModel) return
    setError(""); setSwitchingModel(true)
    try {
      const result = await ai.setModel(connection.token, next)
      if (mounted.current) setConnection({ ...connection, model: result.model })
    } catch (e) {
      if (!mounted.current) return
      const message = (e as Error).message
      // An expired connection cannot be repaired from here; send the user back to the connect form.
      if (/expired/i.test(message)) { setConnection(null); clearAiWorkspace() }
      setError(message)
    } finally { if (mounted.current) setSwitchingModel(false) }
  }
  const disconnect = () => void perform(async () => {
    try { await ai.disconnect(connection!.token) } finally { setConnection(null); clearAiWorkspace(); setContext(null); setShowContext(false); setNewInputs({}) }
  })
  const apply = (proposal: AiProposal, patch: boolean) => perform(async () => {
    if (!connection) return
    const result = await ai.apply(connection.token, proposal.id, p.sessionId, patch, patch ? newInputs[proposal.id] : {})
    if (!mounted.current) return
    if (result.view) p.onGraphChanged(result.view)
    if (result.session || result.session_id) await p.onSessionChanged(result.session?.id || result.session_id!)
    setProposals(old => old.filter(item => item.id !== proposal.id))
    setResolutions(old => ({ ...old, [proposal.id]: result.executed ? "executed" : patch ? "patched" : "saved" }))
    setNewInputs(old => { const next = { ...old }; delete next[proposal.id]; return next })
    const text = result.executed ? "Reviewed action executed." :
      result.session?.patch_pending ? "Graph saved. Session patch queued for the next step boundary." :
      patch ? "Graph saved and session patched." : "Graph saved. The running session is unchanged."
    setTurns(old => old.map((turn, i) => i === old.length - 1 ? { ...turn, events: [...turn.events, { kind: "text", text }] } : turn))
  })
  const review = (proposal: AiProposal, turnId: string, index: number) => {
    if (proposalLocations.get(proposal.id) !== `${turnId}:${index}`) return <button type="button" className="inline-flex items-center gap-1 text-xs text-primary-soft hover:underline"
      onClick={() => document.getElementById(`ai-proposal-${proposal.id}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" })}><Icon name="update" size={14} />View updated proposal</button>
    const current = proposals.find(item => item.id === proposal.id)
    return <AiProposalCard proposal={current || proposal} resolution={resolutions[proposal.id]} busy={busy || !current} dirty={wip.size > 0} sessionId={p.sessionId}
      inputs={newInputs[proposal.id] || {}} onInputs={inputs => setNewInputs(old => ({ ...old, [proposal.id]: inputs }))}
      onApply={patch => void apply(current || proposal, patch)} onDiscard={() => void perform(async () => {
        await ai.discard(connection!.token, proposal.id)
        setProposals(old => old.filter(item => item.id !== proposal.id))
        setResolutions(old => ({ ...old, [proposal.id]: "discarded" }))
        setNewInputs(old => { const next = { ...old }; delete next[proposal.id]; return next })
      })} />
  }

  return <section className="ai-chat relative flex shrink-0 flex-col border-t border-outline-strong/60 bg-recess"
    style={open ? { height: panelHeight } : undefined} aria-label="AI authoring assistant"
    onKeyDown={e => {
      if (e.key !== "Escape" || !controller.current || e.nativeEvent.isComposing) return
      e.preventDefault(); e.stopPropagation(); stop()
    }}>
    {open && <div role="separator" aria-label="Resize AI panel" aria-orientation="horizontal" tabIndex={0}
      aria-valuemin={Math.round(minHeight)} aria-valuemax={Math.round(maxHeight)} aria-valuenow={Math.round(panelHeight)}
      title="Drag up or down to resize; use arrow keys when focused"
      className="group absolute -top-1 left-0 z-20 flex h-2 w-full touch-none cursor-row-resize select-none items-center justify-center hover:bg-primary/30 focus-visible:bg-primary/30 focus-visible:outline-none"
      onPointerDown={e => {
        if (e.button !== 0) return
        e.preventDefault()
        e.currentTarget.focus()
        e.currentTarget.setPointerCapture(e.pointerId)
        resize.current = { y: e.clientY, height: panelHeight }
      }}
      onPointerMove={e => {
        if (resize.current) resizeTo(resize.current.height + resize.current.y - e.clientY)
      }}
      onPointerUp={e => {
        resize.current = null
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
      }}
      onPointerCancel={() => { resize.current = null }}
      onLostPointerCapture={() => { resize.current = null }}
      onKeyDown={e => {
        if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return
        e.preventDefault()
        resizeTo(e.key === "Home" ? minHeight : e.key === "End" ? maxHeight : panelHeight + (e.key === "ArrowUp" ? 32 : -32))
      }}>
      <span className="h-0.5 w-10 rounded bg-outline-strong group-hover:bg-primary-soft" />
    </div>}
    <div className="flex h-11 shrink-0 items-center gap-1 border-b border-outline-variant bg-surface-low/95 pr-2">
    <div className="flex h-full min-w-0 flex-1 items-center gap-2 px-3 text-xs text-on-surface">
      <Icon name="auto_awesome" size={16} className="text-primary-soft" />
      <b className="shrink-0">AI assistant</b>
      <span className="hidden text-[10px] text-faint 2xl:inline">{p.sessionId ? "Authoring + live session" : "Graph authoring"}</span>
      {connection && <AiModelPicker provider={connection.provider} model={connection.model} models={connection.models || []}
        locked={!!controller.current} switching={switchingModel} onSelect={id => void switchModel(id)} />}
      {open && connection && <div className="hidden shrink-0 items-center gap-1 border-l border-outline-variant pl-2 xl:flex">
        <button type="button" className={`${cls.btn} ${showContext ? "border-primary-soft/40 text-primary-soft" : ""}`} aria-pressed={showContext} onClick={previewContext}><Icon name="visibility" size={14} className="text-secondary-soft" />Preview AI context</button>
        <button type="button" className={cls.ghost} disabled={busy} onClick={clearConversation}>Clear conversation</button>
        <button type="button" className={cls.ghost} disabled={busy} onClick={disconnect}>Disconnect</button>
      </div>}
      {!!proposals.length && <button type="button" className="label-caps shrink-0 rounded border border-primary-soft/25 bg-primary-soft/10 px-2 py-1 text-primary-soft" onClick={() => {
        setOpen(true)
        window.requestAnimationFrame(() => document.getElementById(`ai-proposal-${proposals[proposals.length - 1].id}`)?.scrollIntoView({ block: "nearest", behavior: "smooth" }))
      }}>{proposals.length} to review</button>}
      <span className={`ml-auto flex min-w-0 items-center gap-2 ${busy ? "text-secondary-soft" : error || lastTurn?.status === "failed" ? "text-danger-soft" : "text-muted"}`} role="status" aria-live="polite">
        <Icon name={busy ? "progress_activity" : error || lastTurn?.status === "failed" ? "error" : "check_circle"} size={14} className={busy ? "shrink-0 animate-spin" : "shrink-0"} />
        <span className="max-w-40 truncate">{busy ? activity || "Working…" : error || lastTurn?.status === "failed" ? "Needs attention" : lastTurn?.status === "stopped" ? "Stopped" : connection ? "Ready" : "Connect a provider"}</span>
        {currentTurn && <span className="mono shrink-0 tabular-nums">{duration(now - currentTurn.startedAt)}</span>}
      </span>
      {open && lastUsage && !busy && <span className="mono hidden shrink-0 border-l border-outline-variant pl-2 text-[10px] text-faint 2xl:inline">{lastUsage.input_tokens.toLocaleString()} in / {lastUsage.output_tokens.toLocaleString()} out</span>}
    </div>
    {busy && controller.current && !open && <button type="button" className={`${cls.danger} shrink-0`} aria-label="Stop AI response" title="Stop (Esc)" onClick={stop}>
      <Icon name="stop" size={14} />Stop
    </button>}
    <button type="button" className={`${cls.ghost} shrink-0`} aria-label={open ? "Hide AI chat" : "Show AI chat"}
      title={open ? "Hide AI chat" : "Show AI chat"} aria-expanded={open}
      onClick={() => { setOpen(!open); setExpanded(false) }}>
      <Icon name="minimize" size={18} />
    </button>
    <button type="button" className={`${cls.ghost} shrink-0`} aria-label={expanded ? "Restore AI panel size" : "Expand AI panel"}
      title={expanded ? "Restore panel size" : "Expand to almost full screen"} aria-pressed={expanded}
      onClick={() => { setOpen(true); setExpanded(!expanded) }}>
      <Icon name={expanded ? "fullscreen_exit" : "fullscreen"} size={18} />
    </button>
    </div>
    {open && <div className="flex min-h-0 flex-1 flex-col overflow-hidden border-t border-outline-variant">
      {error && <div role="alert" className="mx-3 mt-2 flex shrink-0 items-start gap-2 rounded-lg border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger-soft">
        <Icon name="error" size={16} className="shrink-0" /><span className="min-w-0 flex-1 break-words">{error}</span>
        <button type="button" className={cls.ghost} aria-label="Dismiss AI error" onClick={() => setError("")}><Icon name="close" size={14} /></button>
      </div>}
      {!connection ? <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4 sm:p-6">
      <form className="mx-auto my-auto flex w-full max-w-3xl shrink-0 flex-col items-stretch gap-4" onSubmit={e => {
        e.preventDefault()
        void perform(async () => { const next = await ai.connect(provider, key, triageModel); setKey(""); setConnection(next) }, "Connecting…")
      }}>
        <p className="w-full text-sm text-on-surface">Connect a provider to edit graphs, write code and inspect a live session. Pick the model from the badge in the chat header — you can switch it any time between messages.</p>
        <div className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-end gap-3">
        <label className="flex flex-col gap-1 text-xs text-muted">Provider
          <select className={cls.select} disabled={busy} value={provider} onChange={e => {
            setProvider(e.target.value); setKey(""); setTriageModel(""); setError("")
          }}>
            <option value="openai">OpenAI</option><option value="anthropic">Claude</option>
          </select>
        </label>
        <label className="flex min-w-0 flex-col gap-1 text-xs text-muted">API key
          <input className={cls.input} type="password" autoComplete="off" disabled={busy} value={key}
            onChange={e => setKey(e.target.value)} placeholder="Or use a configured server key" />
        </label>
        <button className={`${cls.primary} whitespace-nowrap`} disabled={busy}>{busy ? "Connecting…" : "Connect"}</button>
        </div>
        <details className="text-xs text-muted"><summary className="cursor-pointer">Context selection</summary>
          <label className="mt-2 flex max-w-sm flex-col gap-1">Context model
            <select className={cls.select} value={triageModel} disabled={busy} onChange={e => setTriageModel(e.target.value)}>
              <option value="">Local relevance (default)</option><option value="auto">Automatic lightweight model</option>
            </select>
          </label><p className="mt-2">The assistant gets a compact view and explores the rest with tools. An optional small model can pre-select context for large graphs.</p>
        </details>
        <p className="w-full text-xs text-muted">Keys and conversation stay in API memory. Connections expire after one idle hour; active responses can run longer.</p>
      </form>
      </div> : <>
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-outline-variant bg-surface-low px-3 py-2 xl:hidden">
          <button className={`${cls.btn} ${showContext ? "border-primary-soft/40 text-primary-soft" : ""}`} aria-pressed={showContext} onClick={previewContext}><Icon name="visibility" size={14} />Preview AI context</button>
          <button className={cls.ghost} disabled={busy} onClick={clearConversation}>Clear conversation</button>
          <button className={cls.ghost} disabled={busy} onClick={disconnect}>Disconnect</button>
        </div>
        <div className="flex min-h-0 flex-1 overflow-hidden">
          <div className="relative flex min-h-0 min-w-0 flex-1 flex-col">
          <div ref={log} className="min-h-0 flex-1 overflow-auto px-4 text-xs" onScroll={e => {
            const node = e.currentTarget
            setFollowing(node.scrollHeight - node.scrollTop - node.clientHeight < 48)
          }}>
            <div className={`ai-chat-column ${turns.length ? "pb-4" : "flex min-h-full items-center justify-center"}`}>
            {!turns.length && <div className="flex flex-col items-center justify-center gap-3 py-5 text-center">
              <span className="inline-flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary-soft"><Icon name="auto_awesome" size={24} /></span>
              <h2 className="text-base font-semibold text-on-surface">What would you like to build?</h2>
            </div>}
            {turns.map(turn => <TurnView key={turn.id} turn={turn} now={now} onRetry={text => { setPrompt(text); composer.current?.focus() }} renderProposal={(proposal, index) => review(proposal, turn.id, index)} />)}
            </div>
          </div>
          {!following && <button type="button" className={`${cls.btn} absolute bottom-3 left-1/2 -translate-x-1/2 shadow-lg`} onClick={() => { setFollowing(true); log.current?.scrollTo({ top: log.current.scrollHeight, behavior: "smooth" }) }}><Icon name="arrow_downward" size={14} />Latest activity</button>}
          </div>
          {showContext && <>
            <div role="separator" aria-label="Resize AI context sidebar" aria-orientation="vertical" tabIndex={0}
              aria-valuemin={Math.round(contextMinWidth)} aria-valuemax={Math.round(contextMaxWidth)} aria-valuenow={Math.round(contextPanelWidth)}
              title="Drag to resize context; use left and right arrow keys when focused"
              className="w-1 shrink-0 cursor-col-resize touch-none bg-outline-variant hover:bg-primary-soft/50 focus-visible:bg-primary-soft/50 focus-visible:outline-none"
              onPointerDown={e => {
                if (e.button !== 0) return
                e.preventDefault(); e.currentTarget.focus(); e.currentTarget.setPointerCapture(e.pointerId)
                contextResize.current = { x: e.clientX, width: contextPanelWidth }
              }}
              onPointerMove={e => { if (contextResize.current) setContextWidth(Math.max(contextMinWidth, Math.min(contextMaxWidth, contextResize.current.width + contextResize.current.x - e.clientX))) }}
              onPointerUp={e => { contextResize.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }}
              onPointerCancel={() => { contextResize.current = null }} onLostPointerCapture={() => { contextResize.current = null }}
              onKeyDown={e => {
                if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return
                e.preventDefault()
                setContextWidth(e.key === "Home" ? contextMinWidth : e.key === "End" ? contextMaxWidth
                  : Math.max(contextMinWidth, Math.min(contextMaxWidth, contextPanelWidth + (e.key === "ArrowLeft" ? 32 : -32))))
              }} />
            <aside aria-label="AI context sidebar" style={{ width: contextPanelWidth }} className="flex min-w-0 shrink-0 flex-col bg-surface-low">
              <div className="flex shrink-0 items-center gap-2 border-b border-outline-variant px-3 py-2 text-xs text-on-surface">
                <Icon name="lock" size={14} className="shrink-0 text-success" /><b className="min-w-0 flex-1 truncate">AI-visible context</b>
                <button type="button" className={cls.ghost} aria-label="Refresh AI context" title="Refresh context" disabled={busy} onClick={refreshContext}><Icon name="refresh" size={15} /></button>
                <button type="button" className={cls.ghost} aria-label="Close context preview" onClick={() => setShowContext(false)}><Icon name="close" size={15} /></button>
              </div>
              <div className="min-h-0 flex-1 overflow-auto p-3">
                {context != null ? <AiCodeBlock text={JSON.stringify(context, null, 2)} language="json" label="Context" maxHeight="max-h-none" wrap />
                  : <p className="text-xs text-muted">{busy ? "Preparing context…" : "Context is unavailable. Refresh to try again."}</p>}
              </div>
            </aside>
          </>}
        </div>
        <div className="shrink-0 border-t border-outline-variant bg-recess px-4 pb-3 pt-2">
          <div className="ai-chat-column">
          <div className="mb-2 flex items-center gap-2 overflow-x-auto py-0.5" aria-label="Quick prompts">
            <span className="label-caps inline-flex shrink-0 items-center gap-1 text-faint"><Icon name="bolt" size={14} className="text-secondary-soft" />Quick</span>
            {QUICK_PROMPTS.map(item => <button key={item.label} type="button" className="mono shrink-0 rounded-full border border-outline-strong/50 bg-surface-container px-2.5 py-1 text-[10px] text-on-surface-variant hover:border-primary-soft/40 hover:text-on-surface" onClick={() => insertPrompt(item.text)}>{item.label}</button>)}
          </div>
          {busy && controller.current && <div className="mb-2 flex items-center gap-2 text-[11px] text-secondary-soft" role="status">
            <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-secondary" />{activity || "Working…"}
            <span className="ml-auto text-muted">{now - lastSignal.current > 15_000 ? "No recent server update · still waiting · Esc stops" : "Connected · updates live · Esc stops"}</span>
          </div>}
          {!!steering.length && <div className="mb-2 max-h-24 space-y-1 overflow-auto" aria-label="Messages sent to the running turn">
            <p className="text-[10px] text-faint">Sent · the assistant reads it at its next step</p>
            {steering.map((text, i) => <div key={i} className="flex items-center gap-2 rounded border border-primary-soft/25 bg-primary-soft/5 px-2 py-1 text-[11px]">
              <Icon name="forward_to_inbox" size={14} className="text-primary-soft" /><span className="min-w-0 flex-1 truncate text-muted" title={text}>{text}</span>
            </div>)}
          </div>}
          {!!queued.length && <div className="mb-2 max-h-24 space-y-1 overflow-auto" aria-label="Queued messages">
            <p className="text-[10px] text-faint">{busy && !queuePaused.current ? "Queued · sent after this turn" : "Queued · waiting for you"}</p>
            {queued.map((text, i) => <div key={i} className="flex items-center gap-2 rounded border border-outline-variant bg-recess px-2 py-1 text-[11px]">
              <Icon name="schedule" size={14} className="text-muted" /><span className="min-w-0 flex-1 truncate text-muted" title={text}>{text}</span>
              {!busy && <button type="button" className={cls.ghost} onClick={() => { queuePaused.current = false; setQueued(old => old.filter((_, index) => index !== i)); void runTurn(text) }}>Send now</button>}
              <button type="button" className={cls.ghost} aria-label={`Remove queued message ${i + 1}`} onClick={() => setQueued(old => old.filter((_, index) => index !== i))}><Icon name="close" size={13} /></button>
            </div>)}
          </div>}
          <form className="rounded-xl border border-outline-strong/60 bg-surface-low p-2 focus-within:border-primary-soft/60 focus-within:ring-1 focus-within:ring-primary-soft/20" onSubmit={e => { e.preventDefault(); send() }}>
            <textarea ref={composer} aria-label="Message the AI assistant" aria-describedby="ai-composer-help" className="block w-full resize-none bg-transparent px-1 py-1 text-[13px] leading-relaxed text-on-surface placeholder:text-faint focus:outline-none" rows={2}
              placeholder={controller.current ? "Add guidance — the assistant reads it at its next step. Esc stops." : "Ask about this graph or describe a change…"}
              value={prompt} onChange={e => setPrompt(e.target.value)} onKeyDown={e => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                  e.preventDefault()
                  if (!e.repeat) send()
                }
              }} />
            <div className="flex items-center gap-2">
              <span className="inline-flex max-w-48 items-center gap-1 truncate text-[10px] text-muted" title={`Graph: ${p.view.name}${p.selected ? ` / ${p.selected}` : ""}`}><Icon name="account_tree" size={13} className="shrink-0 text-secondary-soft" /><span className="truncate">{p.selected || p.view.name}</span></span>
              <span id="ai-composer-help" className="ml-auto min-w-0 text-[10px] text-faint">{controller.current ? "Enter sends to the running turn · Esc stops" : "Enter to send"} · Shift+Enter for a new line</span>
              {controller.current ? <>
                {!!prompt.trim() && <button className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-primary-soft/40 text-primary-soft hover:bg-primary-soft/10" aria-label="Send to running turn" title="Send now; the assistant reads it at its next step (Enter)">
                  <Icon name="arrow_upward" size={18} />
                </button>}
                <button type="button" className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-on-surface text-recess hover:brightness-110" aria-label="Stop AI response" title="Stop (Esc)" onClick={stop}>
                  <Icon name="stop" size={18} />
                </button>
              </> : <button className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary-soft text-recess hover:brightness-110 disabled:opacity-40" aria-label="Send" title="Send message (Enter)" disabled={busy || !prompt.trim()}>
                <Icon name="arrow_upward" size={18} />
              </button>}
            </div>
          </form>
          <p className="mt-1.5 text-center text-[10px] text-faint">Use synthetic examples. Known private values are masked before sending.</p>
          </div>
        </div>
      </>}
    </div>}
  </section>
}
