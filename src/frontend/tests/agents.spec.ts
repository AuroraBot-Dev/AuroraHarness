import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAgentStore } from '~/stores/agentStore'
import { runtimeRequest } from '~/utils/runtimeClient'

vi.mock('~/utils/runtimeClient', () => ({ runtimeRequest: vi.fn() }))
const agent = { id: 'a1', name: '视觉专家', description: '检查截图', instructions: '检查布局', modelConfigId: 'm1', allowedTools: [], enabled: true, callable: true }
const status = { id: 'a1', modelType: 'multimodal', modelName: 'vision', issues: [], runningCount: 1, waitingCount: 0, test: {}, active: [], latest: null }
function respond() {
  vi.mocked(runtimeRequest).mockImplementation(async (method) => {
    if (method === 'agent.list') return { items: [agent] }
    if (method === 'model.list') return { items: [{ id: 'm1', modelType: 'multimodal' }] }
    if (method === 'agent.status') return { items: [status] }
    if (method === 'agent.test') return { status: 'passed' }
    return agent
  })
}

describe('Agent management', () => {
  beforeEach(() => { setActivePinia(createPinia()); vi.mocked(runtimeRequest).mockReset(); respond() })

  it('loads configurations and live execution counts', async () => {
    const store = useAgentStore()
    await store.refresh()
    expect(store.connected).toBe(true)
    expect(store.agents[0]?.description).toBe('检查截图')
    expect(store.statuses[0]?.runningCount).toBe(1)
  })

  it('creates, edits and disables Agents through the existing protocol', async () => {
    const store = useAgentStore()
    await store.save('', { name: '专家', callable: true })
    expect(runtimeRequest).toHaveBeenCalledWith('agent.create', { id: undefined, data: { name: '专家', callable: true } })
    await store.save('a1', { enabled: false })
    expect(runtimeRequest).toHaveBeenCalledWith('agent.update', { id: 'a1', data: { enabled: false } })
    expect((await store.test('a1')).status).toBe('passed')
  })

  it('marks disconnected state unknown and refreshes after reconnect', async () => {
    const store = useAgentStore()
    await store.refresh()
    store.disconnect()
    expect(store.connected).toBe(false)
    expect(store.error).toContain('未知')
    await store.refresh()
    expect(store.connected).toBe(true)
    expect(store.error).toBe('')
  })

  it('does not show a late response as connected after a disconnect', async () => {
    const finish: ((value: unknown) => void)[] = []
    vi.mocked(runtimeRequest).mockImplementation(() => new Promise((resolve) => { finish.push(resolve) }))
    const store = useAgentStore()
    const pending = store.refresh()
    store.disconnect()
    finish.forEach((resolve) => resolve({ items: [] }))
    await pending
    expect(store.connected).toBe(false)
  })

  it('reports failed polling without discarding editable configurations', async () => {
    const store = useAgentStore()
    await store.refresh()
    vi.mocked(runtimeRequest).mockRejectedValue(new Error('offline'))
    await store.refresh()
    expect(store.connected).toBe(false)
    expect(store.agents).toHaveLength(1)
    expect(store.error).toContain('offline')
  })
})
