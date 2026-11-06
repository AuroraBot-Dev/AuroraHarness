<script setup lang="ts">
import { NAlert, NButton, NInput, NSelect, useMessage } from 'naive-ui'
import { Plus, Puzzle, Refresh, Search } from '~/utils/icons'
import type { AgentConfig } from '~/types/workflow'
import { onRuntimeEvent } from '~/utils/runtimeClient'

const store = useAgentStore()
const message = useMessage()
const editing = ref(false)
const duplicating = ref(false)
const selected = ref<AgentConfig | null>(null)
const testing = ref('')
const query = ref('')
const filter = ref('all')
const filters = [
  { label: '全部角色', value: 'all' },
  { label: '已启用', value: 'enabled' },
  { label: '已开放调用', value: 'callable' },
]
const filtered = computed(() =>
  store.agents.filter((agent) => {
    const matchesQuery = `${agent.name} ${agent.description}`
      .toLowerCase()
      .includes(query.value.toLowerCase())
    const matchesFilter =
      filter.value === 'enabled'
        ? agent.enabled
        : filter.value === 'callable'
          ? agent.enabled && agent.callable
          : true
    return matchesQuery && matchesFilter
  }),
)
const availableCount = computed(
  () => store.agents.filter((agent) => agent.enabled && agent.callable).length,
)
const status = (id: string) =>
  store.connected ? store.statuses.find((item) => item.id === id) : undefined
useHead({ title: 'Agent' })

function edit(agent: AgentConfig | null = null, duplicate = false) {
  selected.value = agent
  duplicating.value = duplicate
  editing.value = true
}

async function toggle(agent: AgentConfig) {
  try {
    await store.save(agent.id, { enabled: !agent.enabled })
  } catch (error) {
    message.error(String(error))
  }
}

async function test(id: string) {
  testing.value = id
  try {
    const result = await store.test(id)
    if (result.status === 'passed') message.success('连接测试通过')
    else message.error(result.error ?? '连接测试失败')
  } catch (error) {
    message.error(String(error))
  } finally {
    testing.value = ''
  }
}

let timer: ReturnType<typeof setInterval> | undefined
let unsubscribe: (() => void) | undefined
function refreshVisible() {
  if (!document.hidden) void store.refresh()
}

onMounted(() => {
  refreshVisible()
  timer = setInterval(refreshVisible, 3000)
  document.addEventListener('visibilitychange', refreshVisible)
  unsubscribe = onRuntimeEvent((event) => {
    if (event.type === 'runtime.disconnected') store.disconnect()
  })
})
onBeforeUnmount(() => {
  clearInterval(timer)
  unsubscribe?.()
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>

<template>
  <main class="library-page app-scrollbar">
    <PageHeader
      title="Agent"
      description="把专长交给合适的伙伴。角色、模型、提示词与工具，都由你定义。"
    >
      <NButton
        type="primary"
        @click="edit()"
      >
        <template #icon><AppIcon :component="Plus" /></template>
        新建 Agent
      </NButton>
    </PageHeader>
    <div class="library-content">
      <NAlert
        v-if="store.error"
        type="warning"
        title="状态未知"
      >
        {{ store.error }}
      </NAlert>
      <div class="agent-summary">
        <span>{{ store.agents.length }} 个自定义角色</span>
        <span>{{ availableCount }} 个已启用并开放调用</span>
        <NuxtLink
          to="/settings"
          class="text-link"
        >
          管理模型与供应商 →
        </NuxtLink>
      </div>
      <div class="library-toolbar">
        <NInput
          v-model:value="query"
          :input-props="{ 'aria-label': '搜索 Agent' }"
          placeholder="搜索名称或能力"
          clearable
        >
          <template #prefix>
            <AppIcon
              :component="Search"
              :size="16"
            />
          </template>
        </NInput>
        <div class="toolbar-actions">
          <NSelect
            v-model:value="filter"
            :options="filters"
            aria-label="筛选 Agent"
            class="agent-filter"
          />
          <NButton
            quaternary
            :loading="store.loading"
            @click="store.refresh"
          >
            <template #icon>
              <AppIcon
                :component="Refresh"
                :size="16"
              />
            </template>
            刷新
          </NButton>
        </div>
      </div>
      <div
        v-if="!filtered.length && !store.loading"
        class="empty-library"
      >
        <AppIcon
          :component="Puzzle"
          :size="36"
        />
        <h2>{{ query || filter !== 'all' ? '没有匹配的 Agent' : '遇见你的下一位协作伙伴' }}</h2>
        <p>定义它的专长，选择模型，给它一段好的工作指令。</p>
        <NButton
          secondary
          @click="edit()"
        >
          新建 Agent
        </NButton>
      </div>
      <TransitionGroup
        name="library-card"
        tag="div"
        class="agent-grid"
      >
        <AgentCard
          v-for="agent in filtered"
          :key="agent.id"
          :agent="agent"
          :model="store.models.find((model) => model.id === agent.modelConfigId)"
          :status="status(agent.id)"
          :connected="store.connected"
          :testing="testing"
          @edit="edit(agent)"
          @duplicate="edit(agent, true)"
          @toggle="toggle(agent)"
          @test="test(agent.id)"
        />
      </TransitionGroup>
      <p class="library-note">
        Agent 配置保存在本机数据库。编辑与启停会在下一轮运行生效，每轮保留配置快照。
      </p>
    </div>
    <AgentEditor
      v-model:show="editing"
      :agent="selected"
      :duplicate="duplicating"
    />
  </main>
</template>

<style scoped>
.agent-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 18px;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 12px;
}

.agent-summary a {
  margin-left: auto;
}

.agent-filter {
  width: 140px;
}

.agent-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  gap: 18px;
}

@media (max-width: 680px) {
  .agent-summary {
    gap: 10px 18px;
  }

  .agent-summary a {
    width: 100%;
    margin-left: 0;
  }
}
</style>
