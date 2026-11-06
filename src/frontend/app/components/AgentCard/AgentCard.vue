<script setup lang="ts">
import { NButton, NDropdown } from 'naive-ui'
import { Cpu, Dots, Puzzle } from '~/utils/icons'
import type { AgentConfig, AgentStatus, ModelConfig } from '~/types/workflow'

const props = defineProps<{
  agent: AgentConfig
  model?: ModelConfig
  status?: AgentStatus
  connected: boolean
  testing: string
}>()
const emit = defineEmits<{ edit: []; duplicate: []; toggle: []; test: [] }>()
const actions = computed(() => [
  { label: props.agent.enabled ? '停用' : '启用', key: 'toggle' },
  { label: '复制 Agent', key: 'duplicate' },
])
const runLabels: Record<string, string> = {
  running: '运行中',
  waiting: '等待审批或补充信息',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}
const testLabels = {
  passed: '连接测试通过',
  failed: '连接测试失败',
  stale: '配置已变更，请重新测试',
}

function selectAction(key: string) {
  if (key === 'toggle') emit('toggle')
  else if (key === 'duplicate') emit('duplicate')
}
</script>

<template>
  <article class="agent-card">
    <div class="card-heading">
      <AppIcon
        :component="Puzzle"
        :size="24"
      />
      <span
        class="agent-state"
        :class="{ enabled: agent.enabled }"
      >
        {{ agent.enabled ? '已启用' : '已停用' }}
      </span>
    </div>
    <h2>{{ agent.name }}</h2>
    <p class="description">{{ agent.description || '尚未填写能力描述' }}</p>
    <div class="model-info">
      <AppIcon
        :component="Cpu"
        :size="15"
      />
      <span>{{ model?.name ?? '未绑定模型' }}</span>
      <NuxtLink
        :to="{ path: '/settings', query: { model: agent.modelConfigId } }"
        class="text-link"
      >
        配置
      </NuxtLink>
    </div>
    <p
      v-if="model?.modelName === 'unconfigured'"
      class="issue"
    >
      请先配置模型 ID，或选择已配置的模型。
    </p>
    <p
      v-if="status?.issues.length"
      class="issue"
    >
      {{ status.issues.join('；') }}
    </p>
    <p
      v-if="status?.test.status"
      class="test-state"
      :class="{ passed: status.test.status === 'passed' }"
    >
      {{ testLabels[status.test.status] }}
    </p>
    <p
      v-for="execution in status?.active"
      :key="execution.id"
      class="execution-link"
    >
      <NuxtLink :to="`/sessions/${execution.session_id}#agent-run-${execution.id}`">
        {{ runLabels[execution.status] }}：{{ execution.task }} →
      </NuxtLink>
    </p>
    <details class="agent-details">
      <summary>运行与配置详情</summary>
      <p>模型 ID：{{ model?.modelName ?? '未知' }}</p>
      <p>{{ agent.callable ? '已开放调用' : '未开放动态调用' }}</p>
      <template v-if="status">
        <p>运行中 {{ status.runningCount }} · 等待处理 {{ status.waitingCount }}</p>
        <p
          v-if="status.test.error"
          class="issue"
        >
          {{ status.test.error }}
        </p>
        <NuxtLink
          v-if="status.latest"
          :to="`/sessions/${status.latest.session_id}#agent-run-${status.latest.id}`"
          class="text-link"
        >
          最近执行：{{ runLabels[status.latest.status] ?? status.latest.status }} →
        </NuxtLink>
        <p
          v-if="status.latest"
          class="result-preview"
        >
          {{ status.latest.error || status.latest.output || '任务正在处理' }}
        </p>
      </template>
      <p v-else>运行状态未知</p>
    </details>
    <footer class="agent-actions">
      <NButton
        size="small"
        secondary
        @click="emit('edit')"
      >
        编辑
      </NButton>
      <NButton
        size="small"
        quaternary
        :loading="testing === agent.id"
        :disabled="!!testing || !connected || !!status?.issues.length"
        @click="emit('test')"
      >
        测试连接
      </NButton>
      <NDropdown
        trigger="click"
        :options="actions"
        @select="selectAction"
      >
        <NButton
          size="small"
          quaternary
          circle
          aria-label="Agent 更多操作"
        >
          <template #icon>
            <AppIcon
              :component="Dots"
              :size="20"
            />
          </template>
        </NButton>
      </NDropdown>
    </footer>
  </article>
</template>

<style scoped>
.agent-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 24px;
  border: 1px solid var(--border);
  border-radius: 18px;
  background: var(--surface);
  transition:
    box-shadow 180ms ease,
    transform 180ms ease;
}

.agent-card:hover {
  box-shadow: var(--shadow);
  transform: translateY(-2px);
}

.card-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--text-muted);
}

.agent-state {
  font-size: 10px;
}

.agent-state.enabled {
  color: var(--success);
}

h2 {
  margin: 20px 0 8px;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.03em;
  overflow-wrap: anywhere;
}

p {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.8;
  overflow-wrap: anywhere;
}

.description {
  min-height: 44px;
  margin: 0;
  font-size: 12px;
  white-space: pre-wrap;
}

.model-info {
  display: flex;
  align-items: center;
  gap: 7px;
  margin: 20px 0;
  color: var(--text-muted);
  font-size: 11px;
}

.model-info > span {
  min-width: 0;
  flex: 1;
  overflow-wrap: anywhere;
}

.issue {
  color: var(--danger);
}

.test-state,
.execution-link {
  margin: 0 0 12px;
}

.test-state.passed {
  color: var(--success);
}

.execution-link {
  color: var(--accent);
}

.agent-details {
  margin-bottom: 20px;
  color: var(--text-muted);
  font-size: 11px;
}

.agent-details summary {
  width: fit-content;
  cursor: pointer;
}

.result-preview {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
}

.agent-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 5px;
  margin-top: auto;
  padding-top: 16px;
  border-top: 1px solid var(--border);
}

.agent-actions > :last-child {
  margin-left: auto;
}
</style>
