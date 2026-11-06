<script setup lang="ts">
import { NButton, NDropdown, NInput, NScrollbar, useDialog, useMessage } from 'naive-ui'
import { Dots, Folder, FolderPlus, Plus, X } from '~/utils/icons'
import type { ProjectRecord, SessionRecord, TaskNode } from '~/types/agent'
import { isTauri } from '~/utils/runtimeClient'

const props = withDefaults(
  defineProps<{
    workspace?: ProjectRecord
    sessions: SessionRecord[]
    tasks?: TaskNode[]
  }>(),
  { workspace: undefined, tasks: () => [] },
)
const emit = defineEmits<{ close: []; navigate: []; selectTask: [task: TaskNode] }>()
const projects = useProjectStore()
const sessionStore = useSessionStore()
const route = useRoute()
const dialog = useDialog()
const message = useMessage()
const query = ref('')
const showAll = ref(false)
const projectActions = [
  { label: '重命名', key: 'rename' },
  { label: '删除工作区', key: 'delete' },
]
const choices = computed(() =>
  projects.projects.filter((project) =>
    `${project.name} ${project.path}`.toLowerCase().includes(query.value.toLowerCase()),
  ),
)
const visibleSessions = computed(() => {
  const activeIndex = props.sessions.findIndex((session) => session.id === route.params.sessionId)
  return showAll.value || activeIndex >= 8 ? props.sessions : props.sessions.slice(0, 8)
})

watch(
  () => props.workspace?.id,
  () => {
    showAll.value = false
    query.value = ''
  },
)

async function selectWorkspace(project: ProjectRecord) {
  projects.setActive(project.id)
  await navigateTo(`/projects/${project.id}`)
}

async function openSession(session: SessionRecord) {
  await navigateTo(`/sessions/${session.id}`)
  emit('navigate')
}

async function createConversation() {
  if (!props.workspace) return
  try {
    await openSession(await sessionStore.create('新对话', props.workspace.id))
  } catch (error) {
    message.error(String(error))
  }
}

async function createWorkspace() {
  let path: string | null = null
  if (isTauri()) {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selected = await open({ directory: true, multiple: false })
    path = typeof selected === 'string' ? selected : null
  } else {
    path = window.prompt('输入工作区绝对路径')?.trim() || null
  }
  if (!path) return
  try {
    await selectWorkspace(await projects.create(path))
  } catch (error) {
    message.error(String(error))
  }
}

async function renameWorkspace(workspace: ProjectRecord) {
  const name = window.prompt('输入新的工作区名称', workspace.name)?.trim()
  if (!name || name === workspace.name) return
  try {
    await projects.rename(workspace.id, name)
  } catch (error) {
    message.error(String(error))
  }
}

function removeWorkspace(workspace: ProjectRecord) {
  dialog.warning({
    title: '删除工作区？',
    content: `将删除「${workspace.name}」及其所有对话记录，本机文件不受影响。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await projects.delete(workspace.id)
        if (props.workspace?.id === workspace.id) await navigateTo('/')
        message.success('工作区已删除')
      } catch (error) {
        message.error(String(error))
        return false
      }
    },
  })
}

function selectProjectAction(key: string, project: ProjectRecord) {
  if (key === 'rename') void renameWorkspace(project)
  else removeWorkspace(project)
}
</script>

<template>
  <section
    class="workspace-navigator"
    aria-label="工作区导航"
  >
    <header class="panel-heading">
      <h2>工作区</h2>
      <div class="panel-heading-actions">
        <NButton
          quaternary
          circle
          size="small"
          aria-label="添加工作区"
          title="添加工作区"
          @click="createWorkspace"
        >
          <template #icon>
            <AppIcon
              :component="FolderPlus"
              :size="18"
            />
          </template>
        </NButton>
        <NButton
          quaternary
          circle
          size="small"
          aria-label="关闭工作区面板"
          title="收起"
          @click="emit('close')"
        >
          <template #icon>
            <AppIcon
              :component="X"
              :size="17"
            />
          </template>
        </NButton>
      </div>
    </header>
    <NInput
      v-if="projects.projects.length > 5"
      v-model:value="query"
      :input-props="{ 'aria-label': '搜索工作区' }"
      placeholder="搜索工作区"
      size="small"
      clearable
      class="workspace-search"
    />
    <div class="workspace-choices app-scrollbar">
      <div
        v-for="project in choices"
        :key="project.id"
        class="project-row"
        :class="{ selected: workspace?.id === project.id }"
      >
        <button
          class="workspace-choice"
          :aria-label="`切换到 ${project.name}`"
          :aria-pressed="workspace?.id === project.id"
          :title="project.path"
          @click="selectWorkspace(project)"
        >
          <AppIcon
            :component="Folder"
            :size="17"
            :weight="workspace?.id === project.id ? 'fill' : 'regular'"
          />
          <span>{{ project.name }}</span>
        </button>
        <NDropdown
          trigger="click"
          :options="projectActions"
          @select="(key) => selectProjectAction(String(key), project)"
        >
          <button
            class="project-more"
            :aria-label="`工作区操作：${project.name}`"
            title="工作区操作"
          >
            <AppIcon
              :component="Dots"
              :size="18"
            />
          </button>
        </NDropdown>
      </div>
      <p
        v-if="!choices.length"
        class="workspace-empty"
      >
        {{ query ? '没有匹配的工作区' : '添加一个文件夹，开始工作。' }}
      </p>
    </div>
    <div
      v-if="workspace"
      class="panel-conversations"
    >
      <header class="conversation-heading">
        <div>
          <strong :title="workspace.name">{{ workspace.name }}</strong>
          <span>{{ sessions.length }} 个对话</span>
        </div>
        <NButton
          quaternary
          circle
          size="small"
          aria-label="在当前工作区新建对话"
          title="新建对话"
          @click="createConversation"
        >
          <template #icon>
            <AppIcon
              :component="Plus"
              :size="17"
            />
          </template>
        </NButton>
      </header>
      <NScrollbar class="conversation-scroll">
        <nav
          class="conversation-list"
          aria-label="当前工作区对话"
        >
          <WorkspaceSessionItem
            v-for="session in visibleSessions"
            :key="session.id"
            :session="session"
            @select="openSession"
          />
        </nav>
        <p
          v-if="!sessions.length"
          class="workspace-empty"
        >
          还没有对话，点 ＋ 开始。
        </p>
        <button
          v-if="sessions.length > 8"
          class="show-all"
          @click="showAll = !showAll"
        >
          {{ showAll ? '收起' : `查看全部 ${sessions.length} 个对话` }}
        </button>
      </NScrollbar>
    </div>
    <TaskTreePanel
      v-if="tasks.length"
      :tasks="tasks"
      @select="emit('selectTask', $event)"
    />
  </section>
</template>

<style scoped>
.workspace-navigator {
  display: flex;
  width: 100%;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  padding: 28px 16px 20px;
}

.panel-heading,
.panel-heading-actions {
  display: flex;
  align-items: center;
}

.panel-heading {
  min-height: 40px;
  flex: 0 0 auto;
  justify-content: space-between;
  gap: 12px;
  margin: 0 0 24px 8px;
}

.panel-heading h2 {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
}

.panel-heading-actions {
  gap: 2px;
  color: var(--text-muted);
}

.workspace-search {
  margin-bottom: 12px;
}

.workspace-choices {
  display: grid;
  max-height: 32%;
  flex: 0 0 auto;
  gap: 4px;
  overflow-y: auto;
}

.project-row {
  display: flex;
  align-items: center;
  gap: 2px;
  border-radius: 13px;
}

.project-row.selected {
  background: var(--accent-soft);
}

.workspace-choice {
  display: flex;
  min-width: 0;
  flex: 1;
  align-items: center;
  gap: 10px;
  padding: 12px 10px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  text-align: left;
  font-size: 12px;
}

.workspace-choice span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.selected .workspace-choice {
  color: var(--accent);
}

.project-row:hover:not(.selected) {
  background: var(--glass-fill);
}

.project-more {
  display: grid;
  width: 26px;
  height: 28px;
  flex: 0 0 26px;
  place-items: center;
  padding: 0;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
}

.project-more:hover {
  color: var(--text);
  background: var(--glass-fill);
}

.panel-conversations {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  margin-top: 28px;
  padding-top: 22px;
  border-top: 1px solid var(--glass-edge);
}

.conversation-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 4px 16px 8px;
}

.conversation-heading > div {
  display: grid;
  min-width: 0;
  gap: 5px;
}

.conversation-heading strong {
  overflow: hidden;
  font-size: 11px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conversation-heading span {
  color: var(--text-muted);
  font-size: 10px;
}

.conversation-scroll {
  min-height: 0;
  flex: 1;
}

.conversation-list {
  display: grid;
  gap: 4px;
}

.workspace-empty {
  margin: 10px 8px;
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.8;
}

.show-all {
  padding: 14px 8px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 11px;
  cursor: pointer;
}

@media (max-width: 600px) {
  .workspace-navigator {
    padding-top: 18px;
  }

  .panel-heading {
    margin-bottom: 16px;
  }
}
</style>
