<script setup lang="ts">
import { NButton, NDropdown, NIcon, NScrollbar, NTooltip, useDialog, useMessage } from 'naive-ui'
import { Apps, Bell, ChevronDown, ChevronRight, Clock, Dots, Edit, Folder, FolderPlus, GitPullRequest, Plus, Puzzle, Search, Settings } from '@vicons/tabler'
import { isTauri } from '~/utils/runtimeClient'
import type { ProjectRecord, SessionRecord, TaskNode } from '~/types/agent'

defineProps<{ collapsed?: boolean }>()
const route = useRoute()
const sessions = useSessionStore()
const projects = useProjectStore()
const dialog = useDialog()
const message = useMessage()
const detailTask = ref<TaskNode | null>(null)
const expandedProjects = ref<Record<string, boolean>>({})

const currentSession = computed(() => sessions.getSession?.(String(route.params.sessionId ?? '')) ?? sessions.sessions.find((s) => s.id === route.params.sessionId))
const currentTasks = computed(() => {
  const session = currentSession.value
  const runId = session?.runs.at(-1)?.id
  return runId ? session!.tasks.filter((task) => task.runId === runId) : []
})

const projectGroups = computed(() => {
  const fallbackId = projects.projects[0]?.id
  const groups = new Map<string, { project: ProjectRecord; sessions: SessionRecord[] }>()
  for (const project of projects.projects) groups.set(project.id, { project, sessions: [] })
  for (const session of sessions.sorted) {
    const target = groups.get(session.projectId ?? '') ?? (fallbackId ? groups.get(fallbackId) : undefined)
    target?.sessions.push(session)
  }
  return [...groups.values()]
})

function isExpanded(id: string) { return expandedProjects.value[id] ?? true }
function toggleProject(id: string) { expandedProjects.value[id] = !isExpanded(id) }

function showUnavailable(feature: string) {
  dialog.info({
    title: feature,
    content: '暂未开发，后续版本开放。',
    positiveText: '知道了',
  })
}

async function createConversation() {
  const projectId = String(route.params.projectId ?? currentSession.value?.projectId ?? projects.projects[0]?.id ?? '')
  if (projectId) await createSession(projectId)
  else await navigateTo('/')
}

function projectMenuOptions(project: ProjectRecord) {
  return [
    { label: '新建对话', key: `new:${project.id}` },
    { label: '重命名', key: `rename:${project.id}` },
    { label: '删除项目', key: `delete:${project.id}` },
  ]
}

function sessionMenuOptions(session: SessionRecord) {
  return [
    { label: '重命名', key: `rename:${session.id}` },
    { label: '删除', key: `delete:${session.id}` },
  ]
}

async function createProject() {
  let path: string | null = null
  if (isTauri()) {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selected = await open({ directory: true, multiple: false })
    path = typeof selected === 'string' ? selected : null
  } else path = window.prompt('输入工作区绝对路径')?.trim() || null
  if (!path) return
  try {
    const project = await projects.create(path)
    await navigateTo(`/projects/${project.id}`)
  } catch (error) { message.error(String(error)) }
}

async function createSession(projectId: string) {
  try {
    const session = await sessions.create('新对话', projectId)
    await navigateTo(`/sessions/${session.id}`)
  } catch (error) { message.error(String(error)) }
}

async function handleProjectMenu(key: string, project: ProjectRecord) {
  if (key.startsWith('new:')) { await createSession(project.id); return }
  if (key.startsWith('rename:')) {
    const name = window.prompt('输入新的项目名称', project.name)?.trim()
    if (name && name !== project.name) await projects.rename(project.id, name).catch((error) => message.error(String(error)))
    return
  }
  dialog.warning({
    title: '删除项目？',
    content: `项目「${project.name}」及其所有对话将永久删除。`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: async () => {
      await projects.delete(project.id)
      if (route.params.projectId === project.id) await navigateTo('/')
      message.success('项目已删除')
    },
  })
}

async function handleSessionMenu(key: string, session: SessionRecord) {
  if (key.startsWith('rename:')) {
    const title = window.prompt('输入新的会话标题', session.title)?.trim()
    if (title && title !== session.title) await sessions.rename(session.id, title).catch((error) => message.error(String(error)))
    return
  }
  dialog.warning({
    title: '删除会话？', content: `“${session.title}”及其消息、运行和任务将永久删除。`,
    positiveText: '删除', negativeText: '取消',
    onPositiveClick: async () => {
      await sessions.delete(session.id)
      if (route.params.sessionId === session.id) await navigateTo('/')
      message.success('会话已删除')
    },
  })
}
</script>

<template>
  <aside class="sidebar" :class="{ collapsed }">
    <div class="brand-row">
      <span class="brand-mark"><img src="/logo.svg" alt="Aurora Agent" ></span>
      <strong v-if="!collapsed" class="brand-name">Aurora Agent</strong>
      <template v-if="!collapsed">
        <NButton quaternary circle size="tiny" class="brand-action" aria-label="搜索" @click="showUnavailable('搜索')"><template #icon><NIcon :component="Search" :size="17" /></template></NButton>
        <NButton quaternary circle size="tiny" class="brand-action" aria-label="通知" @click="showUnavailable('通知')"><template #icon><NIcon :component="Bell" :size="17" /></template></NButton>
      </template>
    </div>

    <nav class="primary-nav" aria-label="主导航">
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" aria-label="新对话" @click="createConversation"><template #icon><NIcon :component="Edit" :size="18" /></template><span v-if="!collapsed" class="nav-label">新对话</span><NIcon v-if="!collapsed" class="nav-end" :component="Plus" :size="15" /></NButton>
      </template>新对话</NTooltip>
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" :class="{ 'agent-nav-active': route.path === '/agents' }" aria-label="Agent" @click="navigateTo('/agents')"><template #icon><NIcon :component="Puzzle" :size="18" /></template><span v-if="!collapsed" class="nav-label">Agent</span></NButton>
      </template>Agent</NTooltip>
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" aria-label="Git" @click="showUnavailable('Git')"><template #icon><NIcon :component="GitPullRequest" :size="18" /></template><span v-if="!collapsed" class="nav-label">Git</span></NButton>
      </template>Git · 暂未开发</NTooltip>
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" aria-label="站点" @click="showUnavailable('站点')"><template #icon><NIcon :component="Apps" :size="18" /></template><span v-if="!collapsed" class="nav-label">站点</span></NButton>
      </template>站点 · 暂未开发</NTooltip>
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" aria-label="已安排" @click="showUnavailable('已安排')"><template #icon><NIcon :component="Clock" :size="18" /></template><span v-if="!collapsed" class="nav-label">已安排</span></NButton>
      </template>已安排 · 暂未开发</NTooltip>
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="nav-button" aria-label="插件" @click="showUnavailable('插件')"><template #icon><NIcon :component="Puzzle" :size="18" /></template><span v-if="!collapsed" class="nav-label">插件</span></NButton>
      </template>插件 · 暂未开发</NTooltip>
    </nav>

    <section class="projects-section">
      <div class="section-label">
        <span>工作区</span><NIcon :component="ChevronDown" :size="13" />
        <NButton v-if="!collapsed" quaternary circle size="tiny" class="new-project-button" aria-label="新建项目" @click="createProject"><template #icon><NIcon :component="FolderPlus" :size="15" /></template></NButton>
      </div>
      <NScrollbar class="projects-scroll">
        <div v-for="group in projectGroups" :key="group.project.id" class="project-group">
          <div class="project-header" :class="{ active: route.params.projectId === group.project.id }">
            <button class="project-toggle" :aria-label="isExpanded(group.project.id) ? '折叠项目' : '展开项目'" @click="toggleProject(group.project.id)">
              <NIcon :component="isExpanded(group.project.id) ? ChevronDown : ChevronRight" :size="13" />
            </button>
            <button class="project-name" @click="navigateTo(`/projects/${group.project.id}`)">
              <NIcon :component="Folder" :size="15" />
              <span v-if="!collapsed" class="project-label">{{ group.project.name }}</span>
            </button>
            <NDropdown v-if="!collapsed" trigger="click" :options="projectMenuOptions(group.project)" @select="(key) => handleProjectMenu(String(key), group.project)">
              <span class="project-menu" role="button" aria-label="项目操作" @click.stop><NIcon :component="Dots" :size="15" /></span>
            </NDropdown>
          </div>
          <template v-if="isExpanded(group.project.id)">
            <div v-if="group.sessions.length" class="project-sessions">
              <button v-for="session in group.sessions" :key="session.id" class="session-link" :class="{ active: route.params.sessionId === session.id }" @click="navigateTo(`/sessions/${session.id}`)">
                <span class="session-status" :class="session.status" />
                <span v-if="!collapsed" class="session-title">{{ session.title }}</span>
                <NDropdown v-if="!collapsed" trigger="click" :options="sessionMenuOptions(session)" @select="(key) => handleSessionMenu(String(key), session)">
                  <span class="session-menu" role="button" aria-label="会话操作" @click.stop><NIcon :component="Dots" :size="14" /></span>
                </NDropdown>
              </button>
            </div>
            <p v-else-if="!collapsed" class="empty-project">暂无对话</p>
          </template>
        </div>
      </NScrollbar>
    </section>

    <TaskTreePanel v-if="!collapsed" :tasks="currentTasks" @select="detailTask = $event" />
    <nav class="sidebar-footer" aria-label="应用导航">
      <NTooltip placement="right" :disabled="!collapsed"><template #trigger>
        <NButton quaternary class="settings-nav" :class="{ active: route.path === '/settings' }" aria-label="设置" @click="navigateTo('/settings')"><template #icon><NIcon :component="Settings" :size="17" /></template><span v-if="!collapsed">设置</span></NButton>
      </template>设置</NTooltip>
    </nav>
    <TaskDetailsDrawer v-model:show="detailTask" />
  </aside>
</template>

<style scoped>
.sidebar { display: flex; width: 100%; height: 100%; flex-direction: column; background: transparent; overflow: hidden; }
.brand-row { display: flex; min-height: 56px; align-items: center; gap: 9px; padding: 0 13px; }
.brand-mark { display: grid; width: 28px; height: 28px; flex: 0 0 28px; place-items: center; }
.brand-mark img { width: 24px; height: 24px; }
.brand-name { min-width: 0; flex: 1; font-size: 14px; white-space: nowrap; }
.brand-action { flex: 0 0 auto; color: var(--text-muted); }

.primary-nav { display: grid; gap: 2px; padding: 4px 8px 13px; }
.nav-button { width: 100%; height: 34px; justify-content: flex-start; color: var(--text); }
.primary-nav :deep(.n-button__content) { width: 100%; }
.agent-nav-active { background: var(--surface-hover); }
.nav-label { flex: 1; text-align: left; }
.nav-end { flex: 0 0 auto; color: var(--text-muted); }

.projects-section { display: flex; min-height: 180px; flex: 1; flex-direction: column; padding-bottom: 7px; }
.section-label { display: flex; align-items: center; gap: 7px; padding: 7px 14px; color: var(--text-muted); font-size: 11px; font-weight: 650; letter-spacing: .05em; }
.section-label span { font-size: 11px; }
.section-label > :deep(.n-icon) { color: var(--text-muted); }
.section-label .new-project-button { margin-left: auto; }
.new-project-button { color: var(--text-muted); }
.new-project-button:hover { color: var(--text); }
.projects-scroll { min-height: 0; flex: 1; }
.project-group { display: grid; gap: 1px; padding: 0 8px 7px; }
.project-header { display: flex; align-items: center; gap: 2px; padding: 2px 4px; border-radius: 7px; color: var(--text); }
.project-header:hover, .project-header.active { background: var(--surface-hover); }
.project-toggle { display: grid; width: 20px; height: 26px; place-items: center; padding: 0; border: 0; background: transparent; color: var(--text-muted); cursor: pointer; }
.project-name { display: flex; min-width: 0; flex: 1; align-items: center; gap: 8px; padding: 5px 0; border: 0; background: transparent; color: inherit; cursor: pointer; text-align: left; }
.project-name svg { color: var(--text-muted); }
.project-label { min-width: 0; flex: 1; overflow: hidden; font-size: 12.5px; font-weight: 620; text-overflow: ellipsis; white-space: nowrap; }
.project-menu { display: grid; width: 22px; height: 22px; place-items: center; border-radius: 5px; color: var(--text-muted); opacity: 0; }
.project-header:hover .project-menu { opacity: 1; }
.project-sessions { display: grid; gap: 1px; padding: 2px 0 2px 22px; }
.empty-project { margin: 6px 0 2px 22px; color: var(--text-muted); font-size: 11px; }

.session-link { display: flex; width: 100%; min-width: 0; align-items: center; gap: 8px; padding: 7px; border: 0; border-radius: 7px; background: transparent; color: inherit; cursor: pointer; text-align: left; }
.session-link:hover, .session-link.active { background: var(--surface-hover); }
.session-status { width: 7px; height: 7px; flex: 0 0 7px; border-radius: 50%; background: #a1a1aa; }
.session-status.running, .session-status.queued { background: #2563eb; }
.session-status.waiting { background: #d69e2e; }
.session-status.completed { background: #22a06b; }
.session-status.failed, .session-status.cancelled { background: #d14343; }
.session-title { min-width: 0; flex: 1; overflow: hidden; font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.session-menu { display: grid; width: 22px; height: 22px; place-items: center; border-radius: 5px; color: var(--text-muted); opacity: 0; }
.session-link:hover .session-menu, .session-link.active .session-menu { opacity: 1; }

.sidebar-footer { padding: 7px 8px 9px; border-top: 1px solid var(--border); }
.settings-nav { width: 100%; height: 34px; justify-content: flex-start; gap: 8px; color: var(--text-muted); }
.settings-nav:hover, .settings-nav.active { color: var(--text); background: var(--surface-hover); }
.settings-nav span { font-size: 12.5px; }

.collapsed .brand-row { justify-content: center; padding: 0; }
.collapsed .primary-nav { padding: 4px 8px 10px; }
.collapsed .nav-button { justify-content: center; padding: 0; }
.collapsed .projects-section { min-height: 0; }
.collapsed .section-label { display: none; }
.collapsed .project-toggle { display: none; }
.collapsed .project-name { justify-content: center; }
.collapsed .project-sessions { padding-left: 0; }
.collapsed .session-link { justify-content: center; padding: 8px; }
.collapsed .session-status { flex-basis: 7px; }
.collapsed .sidebar-footer { padding: 7px 8px 9px; }
.collapsed .settings-nav { justify-content: center; padding: 0; }
</style>
