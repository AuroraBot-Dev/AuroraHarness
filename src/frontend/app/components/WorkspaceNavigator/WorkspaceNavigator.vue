<script setup lang="ts">
import { NButton, NInput, NPopover, NScrollbar, useDialog, useMessage } from 'naive-ui'
import { Check, ChevronDown, Edit, Folder, FolderPlus, Trash } from '~/utils/icons'
import type { ProjectRecord, SessionRecord } from '~/types/agent'
import { isTauri } from '~/utils/runtimeClient'

const props = defineProps<{
  collapsed?: boolean
  workspace?: ProjectRecord
  sessions: SessionRecord[]
}>()
const projects = useProjectStore()
const route = useRoute()
const dialog = useDialog()
const message = useMessage()
const pickerOpen = ref(false)
const query = ref('')
const showAll = ref(false)
const mobile = ref(false)
let mobileQuery: MediaQueryList | null = null
const choices = computed(() =>
  projects.projects.filter((project) =>
    `${project.name} ${project.path}`.toLowerCase().includes(query.value.toLowerCase()),
  ),
)
const visibleSessions = computed(() => {
  const activeIndex = props.sessions.findIndex((session) => session.id === route.params.sessionId)
  return showAll.value || activeIndex >= 8 ? props.sessions : props.sessions.slice(0, 8)
})
const initial = computed(() => props.workspace?.name.slice(0, 1).toLocaleUpperCase() || '+')

watch(
  () => props.workspace?.id,
  () => {
    showAll.value = false
    query.value = ''
  },
)

function syncMobile() {
  mobile.value = mobileQuery?.matches ?? false
}
onMounted(() => {
  mobileQuery = window.matchMedia('(max-width: 600px)')
  syncMobile()
  mobileQuery.addEventListener('change', syncMobile)
})
onBeforeUnmount(() => mobileQuery?.removeEventListener('change', syncMobile))

async function selectWorkspace(project: ProjectRecord) {
  pickerOpen.value = false
  projects.setActive(project.id)
  await navigateTo(`/projects/${project.id}`)
}

async function openSession(session: SessionRecord) {
  pickerOpen.value = false
  await navigateTo(`/sessions/${session.id}`)
}

async function createWorkspace() {
  pickerOpen.value = false
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

async function renameWorkspace() {
  const workspace = props.workspace
  if (!workspace) return
  const name = window.prompt('输入新的工作区名称', workspace.name)?.trim()
  if (!name || name === workspace.name) return
  try {
    await projects.rename(workspace.id, name)
  } catch (error) {
    message.error(String(error))
  }
}

function removeWorkspace() {
  const workspace = props.workspace
  if (!workspace) return
  pickerOpen.value = false
  dialog.warning({
    title: '删除工作区？',
    content: `将删除「${workspace.name}」及其所有对话记录，本机文件不受影响。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await projects.delete(workspace.id)
        await navigateTo('/')
        message.success('工作区已删除')
      } catch (error) {
        message.error(String(error))
        return false
      }
    },
  })
}
</script>

<template>
  <section
    class="workspace-navigator"
    :class="{ compact: collapsed }"
    aria-label="工作区导航"
  >
    <p
      v-if="!collapsed"
      class="workspace-heading"
    >
      工作区
    </p>
    <NPopover
      v-model:show="pickerOpen"
      trigger="click"
      :placement="mobile ? 'top-end' : collapsed ? 'right-start' : 'bottom-start'"
      :show-arrow="false"
      :style="{ background: 'transparent', boxShadow: 'none', padding: '0', border: '0' }"
      raw
    >
      <template #trigger>
        <button
          class="workspace-trigger"
          aria-label="切换工作区"
          :aria-expanded="pickerOpen"
          :title="workspace?.name || '选择工作区'"
        >
          <span class="workspace-initial">{{ initial }}</span>
          <AppIcon
            class="mobile-folder"
            :component="Folder"
            :size="18"
          />
          <span
            v-if="!collapsed"
            class="workspace-trigger-copy"
          >
            <strong>{{ workspace?.name || '选择工作区' }}</strong>
            <span>{{ sessions.length }} 个对话</span>
          </span>
          <AppIcon
            v-if="!collapsed"
            class="workspace-chevron"
            :component="ChevronDown"
            :size="13"
          />
          <span class="mobile-label">工作区</span>
        </button>
      </template>
      <div class="workspace-picker">
        <header class="picker-heading">
          <strong>切换工作区</strong>
          <span>{{ projects.projects.length }}</span>
        </header>
        <NInput
          v-if="projects.projects.length > 5"
          v-model:value="query"
          :input-props="{ 'aria-label': '搜索工作区' }"
          placeholder="搜索工作区"
          size="small"
          clearable
        />
        <div class="workspace-choices app-scrollbar">
          <button
            v-for="project in choices"
            :key="project.id"
            class="workspace-choice"
            :aria-label="`切换到 ${project.name}`"
            :aria-pressed="workspace?.id === project.id"
            @click="selectWorkspace(project)"
          >
            <span class="choice-initial">{{ project.name.slice(0, 1).toLocaleUpperCase() }}</span>
            <span class="workspace-choice-copy">
              <strong>{{ project.name }}</strong>
              <span :title="project.path">{{ project.path }}</span>
            </span>
            <AppIcon
              v-if="workspace?.id === project.id"
              :component="Check"
              :size="15"
            />
          </button>
          <p
            v-if="!choices.length"
            class="picker-empty"
          >
            {{ query ? '没有匹配的工作区' : '还没有工作区' }}
          </p>
        </div>
        <section
          v-if="collapsed && sessions.length"
          class="picker-sessions"
          aria-label="当前工作区对话"
        >
          <header class="picker-heading">
            <strong>最近对话</strong>
            <span>{{ workspace?.name }}</span>
          </header>
          <div class="picker-session-list app-scrollbar">
            <WorkspaceSessionItem
              v-for="session in sessions"
              :key="session.id"
              :session="session"
              @select="openSession"
            />
          </div>
        </section>
        <footer class="picker-footer">
          <NButton
            text
            size="small"
            @click="createWorkspace"
          >
            <template #icon>
              <AppIcon
                :component="FolderPlus"
                :size="16"
              />
            </template>
            添加工作区
          </NButton>
          <div
            v-if="workspace"
            class="workspace-management"
          >
            <NButton
              quaternary
              circle
              size="small"
              aria-label="重命名当前工作区"
              title="重命名"
              @click="renameWorkspace"
            >
              <template #icon>
                <AppIcon
                  :component="Edit"
                  :size="15"
                />
              </template>
            </NButton>
            <NButton
              quaternary
              circle
              size="small"
              aria-label="删除当前工作区"
              title="删除"
              @click="removeWorkspace"
            >
              <template #icon>
                <AppIcon
                  :component="Trash"
                  :size="15"
                />
              </template>
            </NButton>
          </div>
        </footer>
      </div>
    </NPopover>
    <template v-if="!collapsed">
      <div
        v-if="workspace"
        class="workspace-conversations"
      >
        <div class="conversation-heading">
          <span>最近对话</span>
          <span>{{ sessions.length }}</span>
        </div>
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
            从「新对话」开始。
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
      <div
        v-else
        class="workspace-empty"
      >
        <p>
          选择一个本机文件夹，
          <br />
          对话会整理在这里。
        </p>
      </div>
    </template>
  </section>
</template>

<style scoped>
.workspace-navigator {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  padding: 0 20px;
}

.workspace-heading {
  margin: 0 0 12px 3px;
  color: var(--text-muted);
  font-size: 10px;
  letter-spacing: 0.04em;
}

.workspace-trigger {
  display: flex;
  width: 100%;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
  padding: 11px;
  border: 1px solid var(--glass-edge);
  border-radius: 17px;
  background: color-mix(in srgb, var(--glass-fill) 70%, transparent);
  color: var(--text);
  cursor: pointer;
  text-align: left;
  transition: background 160ms ease;
}

.workspace-trigger:hover {
  background: var(--glass-fill);
}

.workspace-initial,
.choice-initial {
  display: grid;
  width: 30px;
  height: 30px;
  flex: 0 0 30px;
  place-items: center;
  border-radius: 10px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 12px;
  font-weight: 600;
}

.workspace-trigger-copy,
.workspace-choice-copy {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 3px;
}

.workspace-trigger-copy strong,
.workspace-choice-copy strong {
  overflow: hidden;
  font-size: 11px;
  font-weight: 550;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-trigger-copy > span,
.workspace-choice-copy > span {
  overflow: hidden;
  color: var(--text-muted);
  font-size: 9px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workspace-chevron {
  color: var(--text-muted);
}

.workspace-trigger .mobile-folder,
.mobile-label {
  display: none;
}

.workspace-conversations {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  margin-top: 28px;
}

.conversation-heading,
.picker-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-muted);
  font-size: 10px;
}

.conversation-heading {
  padding: 0 3px 12px;
}

.conversation-scroll {
  min-height: 0;
  flex: 1;
}

.conversation-list {
  display: grid;
  gap: 3px;
}

.workspace-empty,
.picker-empty {
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.8;
}

.workspace-empty {
  margin: 16px 3px;
}

.show-all {
  padding: 12px 6px;
  border: 0;
  background: transparent;
  color: var(--text-muted);
  font-size: 10px;
  cursor: pointer;
}

.workspace-picker {
  width: 300px;
  max-width: calc(100vw - 24px);
  padding: 14px;
  border: 1px solid var(--glass-edge);
  border-radius: 20px;
  background: var(--glass-fill);
  box-shadow: var(--material-shadow);
  -webkit-backdrop-filter: blur(var(--material-blur)) saturate(135%);
  backdrop-filter: blur(var(--material-blur)) saturate(135%);
}

.picker-heading {
  margin: 2px 3px 12px;
}

.picker-heading strong {
  font-size: 11px;
  font-weight: 550;
}

.workspace-choices {
  max-height: 240px;
  overflow-y: auto;
}

.workspace-choice {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  padding: 10px 8px;
  border: 0;
  border-radius: 12px;
  background: transparent;
  color: var(--text);
  text-align: left;
  cursor: pointer;
}

.workspace-choice:hover,
.workspace-choice[aria-pressed='true'] {
  background: var(--accent-soft);
}

.picker-sessions {
  margin-top: 18px;
}

.picker-session-list {
  max-height: 210px;
  overflow-y: auto;
}

.picker-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid var(--border);
}

.workspace-management {
  display: flex;
  gap: 2px;
}

.compact {
  padding: 18px 16px;
}

.compact .workspace-trigger {
  justify-content: center;
  padding: 5px;
  border-radius: 14px;
}

@media (max-width: 600px) {
  .workspace-navigator,
  .workspace-navigator.compact {
    width: 52px;
    height: 58px;
    flex: 0 0 52px;
    padding: 5px 0;
  }

  .workspace-trigger {
    height: 48px;
    flex-direction: column;
    justify-content: center;
    gap: 4px;
    padding: 5px 0 !important;
    border: 0;
    background: transparent;
  }

  .workspace-initial {
    display: none;
  }

  .workspace-trigger .mobile-folder {
    display: inline-block;
  }

  .mobile-label {
    display: block;
    font-size: 9px;
    line-height: 1;
  }

  .workspace-picker {
    max-height: calc(100dvh - 90px);
    overflow-y: auto;
  }
}
</style>
