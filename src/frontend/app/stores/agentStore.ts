import { ref } from "vue"

import { defineStore } from 'pinia'
import type { AgentConfig, AgentStatus, ModelConfig } from '~/types/workflow'
import { runtimeRequest } from '~/utils/runtimeClient'

export const useAgentStore = defineStore('agents', () => {
  const agents = ref<AgentConfig[]>([])
  const models = ref<ModelConfig[]>([])
  const statuses = ref<AgentStatus[]>([])
  const connected = ref(false)
  const loading = ref(false)
  const error = ref('')
  let generation = 0

  function disconnect() {
    generation++
    connected.value = false
    error.value = '运行时连接已断开，状态未知'
  }

  async function refresh() {
    if (loading.value) return
    loading.value = true
    const current = generation
    try {
      const [a, m, s] = await Promise.all([
        runtimeRequest<{ items: AgentConfig[] }>('agent.list'),
        runtimeRequest<{ items: ModelConfig[] }>('model.list'),
        runtimeRequest<{ items: AgentStatus[] }>('agent.status'),
      ])
      if (current !== generation) return
      agents.value = a.items
      models.value = m.items
      statuses.value = s.items
      connected.value = true
      error.value = ''
    } catch (cause) {
      connected.value = false
      error.value = String(cause)
    } finally { loading.value = false }
  }

  async function save(id: string, data: Record<string, unknown>) {
    const result = await runtimeRequest<AgentConfig>(id ? 'agent.update' : 'agent.create', { id: id || undefined, data })
    await refresh()
    return result
  }

  async function test(id: string) {
    const result = await runtimeRequest<AgentStatus['test']>('agent.test', { id })
    await refresh()
    return result
  }

  return { agents, models, statuses, connected, loading, error, refresh, disconnect, save, test }
})
