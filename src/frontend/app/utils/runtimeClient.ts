import { invoke } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import type { RuntimeEvent, WireRequest, WireResponse } from '~/types/agent'
import { PROTOCOL_VERSION } from '~/utils/protocol'

type EventHandler = (event: RuntimeEvent) => void
const handlers = new Set<EventHandler>()
const pending = new Map<string, { resolve: (value: unknown) => void; reject: (reason: Error) => void }>()
let socket: WebSocket | null = null
let tauriUnlisten: UnlistenFn | null = null
let connectPromise: Promise<void> | null = null

export const isTauri = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
const requestId = () => `req_${crypto.randomUUID()}`

function receive(value: unknown) {
  if (!value || typeof value !== 'object') return
  const raw = value as Record<string, unknown>
  if (typeof raw.event_id === 'string') {
    handlers.forEach((handler) => handler(raw as unknown as RuntimeEvent))
    return
  }
  const response = raw as unknown as WireResponse
  const waiter = pending.get(response.request_id)
  if (!waiter) return
  pending.delete(response.request_id)
  if (response.ok) waiter.resolve(response.result)
  else waiter.reject(new Error(response.error?.message ?? '运行时请求失败'))
}

export async function connectRuntime(): Promise<void> {
  if (typeof window === 'undefined') return
  if (connectPromise) return connectPromise
  connectPromise = (async () => {
    if (isTauri()) {
      if (!tauriUnlisten) tauriUnlisten = await listen<RuntimeEvent>('runtime-event', ({ payload }) => receive(payload))
      return
    }
    if (socket?.readyState === WebSocket.OPEN) return
    await new Promise<void>((resolve, reject) => {
      socket = new WebSocket(import.meta.env.VITE_RUNTIME_WS || 'ws://127.0.0.1:8765/ws')
      socket.onopen = () => resolve()
      socket.onerror = () => reject(new Error('无法连接开发运行时'))
      socket.onmessage = ({ data }) => {
        try { receive(JSON.parse(String(data))) } catch { /* 丢弃非协议帧 */ }
      }
      socket.onclose = () => {
        socket = null
        for (const waiter of pending.values()) waiter.reject(new Error('运行时连接已断开'))
        pending.clear()
        handlers.forEach((handler) => handler({
          protocol_version: PROTOCOL_VERSION, event_id: `evt_${crypto.randomUUID()}`, type: 'runtime.disconnected',
          occurred_at: new Date().toISOString(), payload: {},
        }))
      }
    })
  })().finally(() => { connectPromise = null })
  return connectPromise
}

export async function runtimeRequest<T>(method: string, params: Record<string, unknown> = {}): Promise<T> {
  await connectRuntime()
  const request: WireRequest = { protocol_version: PROTOCOL_VERSION, request_id: requestId(), method, params }
  if (isTauri()) {
    const response = await invoke<WireResponse<T>>('runtime_request', { request })
    if (!response.ok) throw new Error(response.error?.message ?? '运行时请求失败')
    return response.result as T
  }
  if (!socket || socket.readyState !== WebSocket.OPEN) throw new Error('运行时未连接')
  return await new Promise<T>((resolve, reject) => {
    pending.set(request.request_id, { resolve: (value) => resolve(value as T), reject })
    socket!.send(JSON.stringify(request))
  })
}

export async function restartRuntimeBroker(): Promise<void> {
  if (isTauri()) { await invoke('runtime_restart'); return }
  socket?.close()
  socket = null
  await connectRuntime()
}

export function onRuntimeEvent(handler: EventHandler): () => void {
  handlers.add(handler)
  return () => handlers.delete(handler)
}
