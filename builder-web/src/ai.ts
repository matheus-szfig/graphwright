import type { GraphView, SessionSummary } from "./api"

const BASE = `${import.meta.env.BASE_URL.replace(/\/$/, "")}/api/ai`
export interface AiConnection { token: string; provider: string; model: string; expires_in: number; triage_model?: string; models?: AiModel[] }
export interface AiModel { id: string; label: string }
export interface AiProposal {
  id: string; kind: "graph" | "action"; graph: string; diff: string; errors: string[]
  action?: string; name?: string; yaml?: string
  new_inputs?: Record<string, { type?: string; required?: boolean; pii?: boolean; credential?: string; default?: unknown }>
}
export interface AiContextRequest {
  graph: string; session_id?: string; selected?: string; values: Record<string, string>; private_values: string[]
}
export interface AiEvent {
  kind: "context" | "text" | "text_delta" | "activity" | "model_done" | "tool_start" | "tool_end" | "proposal" | "done" | "error" | "cancelled" | "heartbeat" | "steer"
  text?: string; message?: string; context?: unknown; proposal?: AiProposal
  sequence?: number; elapsed_ms?: number; duration_ms?: number; round?: number
  tool_id?: string; name?: string; arguments?: string; result?: string; ok?: boolean
  phase?: string; reason?: "complete" | "limit" | "output_limit"
  usage?: { input_tokens: number; output_tokens: number; cached_input_tokens?: number }
  context_stats?: { source_chars: number; selected_chars: number; included_resources: number; catalog_resources: number }
}

export function appendAiEvent(events: AiEvent[], event: AiEvent): AiEvent[] {
  if ((event.kind === "text_delta" || event.kind === "text") && event.round != null) {
    const index = events.findIndex(item => item.kind === "text" && item.round === event.round)
    if (index >= 0) return events.map((item, i) => i === index
      ? { ...item, text: event.kind === "text_delta" ? (item.text || "") + (event.text || "") : event.text } : item)
    return [...events, { ...event, kind: "text" }]
  }
  return [...events, event]
}

async function request<T>(path: string, token: string | undefined, body?: unknown, method = "POST"): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      method, signal: AbortSignal.timeout(path === "/connect" ? 70_000 : path === "/cancel" ? 5_000 : 30_000),
      headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (error) {
    throw new Error((error as Error).name === "TimeoutError"
      ? "The builder API did not respond in time. Check the connection and try again."
      : "Could not reach the builder API. Check that make builder:local is running and try again.")
  }
  if (response.status === 204) return undefined as T
  if (response.status === 404 && (path === "/models" || path === "/model" || path === "/connect")) {
    throw new Error("Builder API needs restarting. Restart make builder:local, then try connecting again.")
  }
  if (response.status === 502 || response.status === 503 || response.status === 504) {
    throw new Error("Builder API is unavailable. Check that make builder:local is running, then try again.")
  }
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail :
    response.status === 422 ? "The API rejected this request (HTTP 422). Check the provider, API key and selected model, then try again." :
    `AI request failed (HTTP ${response.status})`)
  if (data === null) throw new Error("Builder API returned an invalid response. Check the API server and try again.")
  return data as T
}

export const ai = {
  models: (provider: string, key: string) => request<{ models: AiModel[] }>("/models", undefined, { provider, key }),
  connect: (provider: string, key: string, triage_model = "", model = "") => request<AiConnection>("/connect", undefined, { provider, model, key, triage_model }),
  setModel: (token: string, model: string) => request<{ model: string }>("/model", token, { model }),
  disconnect: (token: string) => request<void>("/connection", token, undefined, "DELETE"),
  clear: (token: string) => request<void>("/clear", token),
  cancel: (token: string) => request<void>("/cancel", token),
  steer: (token: string, message: string) => request<{ accepted: boolean }>("/steer", token, { message }),
  context: (token: string, body: AiContextRequest) => request<unknown>("/context", token, body),
  discard: (token: string, id: string) => request<void>(`/proposals/${id}`, token, undefined, "DELETE"),
  apply: (token: string, id: string, session_id?: string, patch = false, inputs: Record<string, string> = {}) =>
    request<{ view?: GraphView; session?: SessionSummary; executed?: boolean; session_id?: string }>(
      `/proposals/${id}/apply`, token, { session_id, patch, inputs }),
  async turn(token: string, body: AiContextRequest & { message: string }, signal: AbortSignal, onEvent: (event: AiEvent) => void) {
    const response = await fetch(`${BASE}/turn`, {
      method: "POST", signal, headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
    })
    if (!response.ok) {
      const data = await response.json().catch(() => null)
      throw new Error(typeof data?.detail === "string" ? data.detail : `AI request failed (HTTP ${response.status}). Try again.`)
    }
    if (!response.body) throw new Error("Streaming is unavailable")
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let pending = ""
    let terminal = false
    const dispatch = (frame: string) => {
      const data = frame.split("\n").filter(line => line.startsWith("data:")).map(line => line.slice(5).trimStart()).join("\n")
      if (!data) return
      const event = JSON.parse(data) as AiEvent
      if (["done", "error", "cancelled"].includes(event.kind)) terminal = true
      onEvent(event)
    }
    try {
      while (true) {
        const { value, done } = await reader.read()
        pending += decoder.decode(value, { stream: !done })
        pending = pending.replace(/\r\n/g, "\n")
        let end: number
        while ((end = pending.indexOf("\n\n")) >= 0) {
          const frame = pending.slice(0, end)
          pending = pending.slice(end + 2)
          dispatch(frame)
        }
        if (terminal) break
        if (done) { if (pending.trim()) dispatch(pending); break }
      }
      if (!terminal) throw new Error("Connection interrupted before the assistant finished. Review the activity below and try again.")
    } finally {
      await reader.cancel().catch(() => undefined)
      reader.releaseLock()
    }
  },
}
