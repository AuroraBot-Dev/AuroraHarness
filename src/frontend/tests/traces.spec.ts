import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick, watchEffect } from 'vue'
import { useTraceStore } from '~/stores/traceStore'
import { runtimeRequest } from '~/utils/runtimeClient'
import { formatTraceDuration, normalizeToolCall, traceInput, tracePreview } from '~/utils/trace'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))
const call = { id: 'call-1', run_id: 'run', agent_run_id: 'role', tool_name: 'write_file', input: { path: 'index.html' }, status: 'running', started_at: '2026-11-07T10:00:00Z' }
const event = (payload: Record<string, unknown>, type = 'tool.started') => ({ protocol_version: 1 as const, event_id: crypto.randomUUID(), occurred_at: '', run_id: 'run', type, payload })

describe('tool call traces', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.mocked(runtimeRequest).mockReset() })

  it('updates subscribers when an unseen historical run finishes its first load', async () => {
    const traces = useTraceStore()
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest).mockImplementation(() => new Promise(done => { resolve = done }))
    const pending = traces.load('unseen')
    let state = ''
    const stop = watchEffect(() => { const trace = traces.ensure('unseen'); state = `${trace.loading}/${trace.loaded}` })
    expect(state).toBe('true/false')
    resolve({ toolCalls: [], agentRuns: [] })
    await pending
    await nextTick()
    expect(state).toBe('false/true')
    stop()
  })

  it('merges live completion during snapshot loading without losing it to the older snapshot', async () => {
    const traces = useTraceStore()
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest).mockImplementation(() => new Promise(done => { resolve = done }))
    traces.handleEvent(event(call))
    const pending = traces.load('run')
    traces.handleEvent(event({ ...call, status: 'completed', output: 'written', duration_ms: 0, ended_at: '2026-11-07T10:00:01Z' }, 'tool.completed'))
    resolve({ toolCalls: [{ ...call, task_id: 'task' }], agentRuns: [{ id: 'role', agentName: '开发者', status: 'running' }] })
    await pending
    const saved = traces.runs.run!.calls[0]!
    expect(saved.status).toBe('completed')
    expect(saved.output).toBe('written')
    expect(saved.durationMs).toBe(0)
    expect(saved.taskId).toBe('task')
    expect(traces.runs.run!.stages[0]?.agentName).toBe('开发者')
  })

  it('deduplicates replayed events and separates runs', () => {
    const traces = useTraceStore()
    traces.handleEvent(event(call))
    traces.handleEvent(event(call))
    traces.handleEvent({ ...event({ ...call, id: 'call-2' }), run_id: 'other' })
    expect(traces.runs.run!.calls).toHaveLength(1)
    expect(traces.runs.other!.calls).toHaveLength(1)
    traces.remove(['run'])
    expect(traces.runs.run).toBeUndefined()
    expect(traces.runs.other).toBeDefined()
  })

  it('queues one refresh when stages change during an in-flight snapshot', async () => {
    const traces = useTraceStore()
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest)
      .mockImplementationOnce(() => new Promise(done => { resolve = done }))
      .mockResolvedValue({ toolCalls: [{ ...call, status: 'completed' }], agentRuns: [] })
    const pending = traces.load('run')
    await traces.load('run')
    await traces.load('run')
    expect(runtimeRequest).toHaveBeenCalledTimes(1)
    resolve({ toolCalls: [], agentRuns: [] })
    await pending
    await nextTick()
    expect(runtimeRequest).toHaveBeenCalledTimes(2)
    expect(traces.runs.run!.calls[0]?.status).toBe('completed')
  })

  it('does not restore deleted session history from a late snapshot response', async () => {
    const traces = useTraceStore()
    let resolve!: (value: unknown) => void
    vi.mocked(runtimeRequest).mockImplementation(() => new Promise(done => { resolve = done }))
    const pending = traces.load('run')
    traces.remove(['run'])
    resolve({ toolCalls: [call], agentRuns: [] })
    await pending
    expect(traces.runs.run).toBeUndefined()
  })

  it('keeps cached records and reports failed reloads without claiming a successful empty history', async () => {
    const traces = useTraceStore()
    traces.handleEvent(event(call))
    vi.mocked(runtimeRequest).mockRejectedValueOnce(new Error('连接中断')).mockResolvedValueOnce({ toolCalls: [], agentRuns: [] })
    await traces.load('run')
    expect(traces.runs.run!.error).toBe('连接中断')
    expect(traces.runs.run!.loaded).toBe(false)
    expect(traces.runs.run!.calls).toHaveLength(1)
    await traces.load('run')
    expect(traces.runs.run!.error).toBe('')
    expect(traces.runs.run!.loaded).toBe(true)
  })

  it('normalizes wire forms and displays actual zero and unknown durations correctly', () => {
    expect(normalizeToolCall({ tool_call_id: 'x', input_json: { path: 'a' }, duration_ms: 0 }).durationMs).toBe(0)
    expect(normalizeToolCall({ id: 'x', durationMs: null }).durationMs).toBeNull()
    expect(formatTraceDuration(0)).toBe('<1 ms')
    expect(formatTraceDuration(null)).toBe('—')
    expect(formatTraceDuration(1223)).toBe('1.2 s')
    expect(formatTraceDuration(61000)).toBe('1 m 1 s')
    expect(traceInput('{"path":"a"}')).toBe('{\n  "path": "a"\n}')
    expect(traceInput('<script>')).toBe('<script>')
    expect(tracePreview({ code: 'print(1)\nprint(2)' })).toBe('print(1) print(2)')
  })
})
