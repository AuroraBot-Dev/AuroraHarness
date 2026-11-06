<script setup lang="ts">
import { NDropdown, useDialog, useMessage } from 'naive-ui'
import { Dots } from '~/utils/icons'
import type { SessionRecord } from '~/types/agent'

const props = defineProps<{ session: SessionRecord }>()
const emit = defineEmits<{ select: [session: SessionRecord] }>()
const route = useRoute()
const sessions = useSessionStore()
const dialog = useDialog()
const message = useMessage()
const actions = [
  { label: '重命名', key: 'rename' },
  { label: '删除', key: 'delete' },
]
const labels: Record<string, string> = {
  idle: '就绪',
  queued: '排队中',
  running: '运行中',
  waiting: '等待处理',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
}

async function selectAction(key: string) {
  const session = props.session
  if (key === 'rename') {
    const title = window.prompt('输入新的会话标题', session.title)?.trim()
    if (!title || title === session.title) return
    try {
      await sessions.rename(session.id, title)
    } catch (error) {
      message.error(String(error))
    }
    return
  }
  dialog.warning({
    title: '删除会话？',
    content: `“${session.title}”及其消息、运行和任务将永久删除。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await sessions.delete(session.id)
        if (route.params.sessionId === session.id) await navigateTo('/')
        message.success('会话已删除')
      } catch (error) {
        message.error(String(error))
        return false
      }
    },
  })
}
</script>

<template>
  <div
    class="session-row"
    :class="{ active: route.params.sessionId === session.id }"
  >
    <button
      class="session-link"
      :title="session.title"
      :aria-current="route.params.sessionId === session.id ? 'page' : undefined"
      @click="emit('select', session)"
    >
      <span
        class="session-status"
        :class="session.status"
        :aria-label="labels[session.status]"
      />
      <span>{{ session.title }}</span>
    </button>
    <NDropdown
      trigger="click"
      :options="actions"
      @select="selectAction"
    >
      <button
        class="session-more"
        :aria-label="`对话操作：${session.title}`"
        title="对话操作"
      >
        <AppIcon
          :component="Dots"
          :size="16"
        />
      </button>
    </NDropdown>
  </div>
</template>

<style scoped>
.session-row {
  display: flex;
  align-items: center;
  border-radius: 9px;
}

.session-row:hover,
.session-row.active,
.session-row:focus-within {
  background: color-mix(in srgb, var(--glass-fill) 70%, transparent);
}

.session-link {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 9px;
  padding: 9px 6px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  text-align: left;
  font-size: 11px;
  cursor: pointer;
}

.active .session-link,
.session-link:hover {
  color: var(--text);
}

.session-link > span:last-child {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.session-status {
  width: 4px;
  height: 4px;
  flex: 0 0 4px;
  border-radius: 50%;
  background: var(--text-muted);
}

.session-status.running,
.session-status.queued {
  background: var(--accent);
}

.session-status.waiting {
  background: #d69e2e;
}

.session-status.completed {
  background: var(--success);
}

.session-status.failed {
  background: var(--danger);
}

.session-more {
  display: grid;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  opacity: 0.6;
}

.session-row:hover .session-more,
.session-row:focus-within .session-more {
  opacity: 1;
}

@media (hover: none) {
  .session-more {
    opacity: 1;
  }
}
</style>
