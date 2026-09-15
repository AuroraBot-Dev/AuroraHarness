import { beforeEach, describe, expect, it, vi } from 'vitest'

class FakeWebSocket {
  static OPEN = 1
  static instance: FakeWebSocket
  readyState = FakeWebSocket.OPEN
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onclose: (() => void) | null = null
  sent: string[] = []

  constructor(readonly url: string) {
    FakeWebSocket.instance = this
  }

  send(value: string) { this.sent.push(value) }
  close() { this.onclose?.() }
}

describe('runtime client websocket transport', () => {
  beforeEach(() => {
    vi.resetModules()
    vi.stubGlobal('WebSocket', FakeWebSocket)
  })

  it('sends a versioned request and resolves its matching response', async () => {
    const client = await import('~/utils/runtimeClient')
    const connecting = client.connectRuntime()
    FakeWebSocket.instance.onopen?.()
    await connecting

    const response = client.runtimeRequest<{ ready: boolean }>('runtime.initialize')
    await vi.waitFor(() => expect(FakeWebSocket.instance.sent).toHaveLength(1))
    const request = JSON.parse(FakeWebSocket.instance.sent[0]!)
    expect(request.protocol_version).toBe(1)
    FakeWebSocket.instance.onmessage?.({
      data: JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result: { ready: true } }),
    })
    await expect(response).resolves.toEqual({ ready: true })
  })

  it('delivers events to subscribers', async () => {
    const client = await import('~/utils/runtimeClient')
    const handler = vi.fn()
    client.onRuntimeEvent(handler)
    const connecting = client.connectRuntime()
    FakeWebSocket.instance.onopen?.()
    await connecting
    FakeWebSocket.instance.onmessage?.({
      data: JSON.stringify({ protocol_version: 1, event_id: 'e1', type: 'run.started', occurred_at: '2026-01-01', payload: {} }),
    })
    expect(handler).toHaveBeenCalledOnce()
  })
})
