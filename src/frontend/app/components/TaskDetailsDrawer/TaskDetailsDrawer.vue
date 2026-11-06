<script setup lang="ts">
import {
  NButton,
  NDescriptions,
  NDescriptionsItem,
  NDrawer,
  NDrawerContent,
  NTag,
  useMessage,
} from 'naive-ui'
import type { TaskNode } from '~/types/agent'

const model = defineModel<TaskNode | null>('show', { required: true })
const runtime = useRuntimeStore()
const sessions = useSessionStore()
const message = useMessage()
const sessionTasks = computed(() =>
  model.value
    ? (sessions
        .getSession(model.value.sessionId)
        ?.tasks.filter((item) => item.runId === model.value?.runId) ?? [])
    : [],
)
const task = computed(
  () => sessionTasks.value.find((item) => item.id === model.value?.id) ?? model.value,
)
const labels: Record<TaskNode['status'], string> = {
  queued: '排队中',
  running: '执行中',
  waiting: '等待输入',
  completed: '已完成',
  failed: '失败',
  blocked: '已阻断',
  cancelled: '已取消',
  interrupted: '已中断',
}
const prerequisites = computed(() => {
  const selected = task.value
  if (!selected) return []
  return [...new Set([...selected.dependsOn, ...selected.blockedBy])].map((id) => {
    const name = sessionTasks.value.find((item) => item.id === id)?.description ?? '待载入的任务'
    return selected.status === 'blocked' && selected.blockedBy.includes(id)
      ? `${name}（未通过）`
      : name
  })
})
const canRetry = computed(
  () =>
    !!task.value && ['failed', 'blocked', 'cancelled', 'interrupted'].includes(task.value.status),
)
const unavailable = computed(
  () =>
    runtime.connectionStatus !== 'connected' ||
    !model.value ||
    sessions.isRunning(model.value.sessionId) ||
    runtime.retryPendingFor(model.value.sessionId),
)

async function retryTask() {
  const selected = task.value
  if (!selected) return
  try {
    const run = await runtime.retryRun(selected.sessionId, selected.runId, selected.id)
    if (run) model.value = null
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}
</script>

<template>
  <NDrawer
    :show="!!model"
    :width="420"
    placement="right"
    @update:show="
      (show) => {
        if (!show) model = null
      }
    "
  >
    <NDrawerContent
      v-if="task"
      title="任务详情"
      closable
    >
      <NDescriptions
        :column="1"
        label-placement="top"
        size="small"
      >
        <NDescriptionsItem label="任务">{{ task.description }}</NDescriptionsItem>
        <NDescriptionsItem label="状态">
          <NTag
            size="small"
            :bordered="false"
          >
            {{ labels[task.status] }}
          </NTag>
        </NDescriptionsItem>
        <NDescriptionsItem
          v-if="prerequisites.length"
          label="前置任务"
        >
          {{ prerequisites.join('、') }}
        </NDescriptionsItem>
        <NDescriptionsItem label="推理强度">{{ task.effort }}</NDescriptionsItem>
        <NDescriptionsItem
          v-if="task.status !== 'blocked'"
          label="输出"
        >
          <pre>{{ task.output || '暂无输出' }}</pre>
        </NDescriptionsItem>
        <NDescriptionsItem
          v-if="task.error && (task.status !== 'blocked' || !task.blockedBy.length)"
          label="错误"
        >
          <span class="error">{{ task.error }}</span>
        </NDescriptionsItem>
      </NDescriptions>
      <NButton
        v-if="canRetry"
        :disabled="unavailable"
        @click="retryTask"
      >
        {{ task.status === 'blocked' ? '重试前置任务并继续' : '重试此任务' }}
      </NButton>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: var(--font-ui);
  font-size: 12px;
  line-height: 1.65;
}
.error {
  color: #c33f3f;
}
</style>
