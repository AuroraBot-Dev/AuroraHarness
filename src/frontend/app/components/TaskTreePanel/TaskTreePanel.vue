<script setup lang="ts">
import { NEmpty, NIcon, NScrollbar } from 'naive-ui'
import { GitBranch } from '@vicons/tabler'
import type { TaskNode } from '~/types/agent'

const props = defineProps<{ tasks: TaskNode[] }>()
const emit = defineEmits<{ select: [task: TaskNode] }>()
const selectedId = ref<string | null>(null)
const roots = computed(() => props.tasks.filter((task) => task.parentId === null))

function selectTask(taskId: string) {
  selectedId.value = taskId
  document.querySelector(`[data-task-id="${CSS.escape(taskId)}"]`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  const task = props.tasks.find((item) => item.id === taskId)
  if (task) emit('select', task)
}
</script>

<template>
  <section class="task-tree-panel">
    <header class="tree-header">
      <span class="tree-title"><NIcon :component="GitBranch" :size="15" /> 委派树</span>
      <span v-if="tasks.length" class="tree-count">{{ tasks.length }}</span>
    </header>
    <NScrollbar class="tree-scroll">
      <ul v-if="roots.length" class="task-tree">
        <TaskTreeBranch
          v-for="root in roots"
          :key="root.id"
          :task="root"
          :tasks="tasks"
          :selected-id="selectedId"
          @select="selectTask"
        />
      </ul>
      <NEmpty v-else size="small" description="新任务将在这里展开" class="tree-empty" />
    </NScrollbar>
  </section>
</template>

<style scoped>
.task-tree-panel { display: flex; min-height: 180px; flex: 1; flex-direction: column; border-top: 1px solid var(--border); }
.tree-header { display: flex; align-items: center; justify-content: space-between; padding: 12px 14px 8px; color: var(--text-muted); }
.tree-title { display: inline-flex; align-items: center; gap: 7px; font-size: 11px; font-weight: 650; letter-spacing: .06em; text-transform: uppercase; }
.tree-count { min-width: 20px; padding: 1px 6px; border-radius: 99px; background: var(--surface-hover); font-size: 10px; text-align: center; }
.tree-scroll { min-height: 0; flex: 1; }
.task-tree { margin: 0; padding: 0 9px 16px; }
.tree-empty { margin: 28px 12px; }

@media (max-width: 860px) {
  .tree-title { font-size: 0; }
  .tree-title :deep(svg) { width: 18px; height: 18px; }
  .tree-count { display: none; }
  .task-tree-panel :deep(.task-name), .task-tree-panel :deep(.effort-tag), .task-tree-panel :deep(.toggle-button) { display: none; }
  .task-tree-panel :deep(.toggle-placeholder) { width: 8px; flex-basis: 8px; }
  .task-tree-panel :deep(.task-button) { justify-content: center; padding: 8px; }
  .task-tree-panel :deep(.tree-children) { margin-left: 12px; padding-left: 0; border: 0; }
  .tree-empty { display: none; }
}
</style>
