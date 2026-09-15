<script setup lang="ts">
import { NIcon, NTag } from 'naive-ui'
import { ChevronDown, ChevronRight } from '@vicons/tabler'
import type { TaskNode } from '~/types/agent'

const props = defineProps<{
  task: TaskNode
  tasks: TaskNode[]
  selectedId?: string | null
}>()
const emit = defineEmits<{ select: [taskId: string] }>()
const expanded = ref(true)
const children = computed(() => props.tasks.filter((task) => task.parentId === props.task.id))
</script>

<template>
  <li class="tree-branch">
    <div class="tree-row" :class="{ selected: selectedId === task.id }">
      <button
        v-if="children.length"
        class="toggle-button"
        :aria-label="expanded ? '折叠任务' : '展开任务'"
        @click="expanded = !expanded"
      >
        <NIcon :component="expanded ? ChevronDown : ChevronRight" :size="14" />
      </button>
      <span v-else class="toggle-placeholder" />
      <button class="task-button" @click="emit('select', task.id)">
        <span class="task-status" :class="task.status" />
        <span class="task-name" :title="task.description">{{ task.description }}</span>
        <NTag size="tiny" :bordered="false" class="effort-tag">
          {{ task.effort }}
        </NTag>
      </button>
    </div>
    <ul v-if="expanded && children.length" class="tree-children">
      <TaskTreeBranch
        v-for="child in children"
        :key="child.id"
        :task="child"
        :tasks="tasks"
        :selected-id="selectedId"
        @select="emit('select', $event)"
      />
    </ul>
  </li>
</template>

<style scoped>
.tree-branch { position: relative; list-style: none; }
.tree-row { display: flex; align-items: center; min-height: 30px; border-radius: 6px; }
.tree-row:hover, .tree-row.selected { background: var(--surface-hover); }
.toggle-button, .task-button { border: 0; background: transparent; color: inherit; cursor: pointer; }
.toggle-button { display: grid; width: 24px; height: 28px; place-items: center; padding: 0; color: var(--text-muted); }
.toggle-placeholder { width: 24px; flex: 0 0 24px; }
.task-button { display: flex; min-width: 0; flex: 1; align-items: center; gap: 8px; padding: 5px 7px 5px 0; text-align: left; }
.task-status { width: 8px; height: 8px; flex: 0 0 8px; border: 1.5px solid #a1a1aa; border-radius: 50%; background: var(--sidebar-bg); }
.task-status.running { border-color: #2563eb; background: #2563eb; box-shadow: 0 0 0 3px rgb(37 99 235 / 12%); }
.task-status.waiting { border-color: #d69e2e; background: #d69e2e; }
.task-status.completed { border-color: #22a06b; background: #22a06b; }
.task-status.failed { border-color: #d14343; background: #d14343; }
.task-status.cancelled { border-color: #71717a; background: #71717a; }
.task-name { min-width: 0; flex: 1; overflow: hidden; color: var(--text); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.effort-tag { color: var(--text-muted); font-size: 9px; }
.tree-children { position: relative; margin: 0 0 0 12px; padding: 0 0 0 12px; border-left: 1px solid var(--border); }
.tree-children :deep(.tree-row)::before { position: absolute; left: -12px; width: 11px; border-top: 1px solid var(--border); content: ''; }
</style>
