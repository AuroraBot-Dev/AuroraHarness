<script setup lang="ts">
import { useMessage } from 'naive-ui'
import { Edit, FileText, Layout, Plus, Puzzle, Settings } from '~/utils/icons'
import type { TaskNode } from '~/types/agent'

defineProps<{ collapsed?: boolean }>()
const route = useRoute()
const sessions = useSessionStore()
const projects = useProjectStore()
const message = useMessage()
const detailTask = ref<TaskNode | null>(null)
const currentSession = computed(() => sessions.getSession(String(route.params.sessionId ?? '')))
const currentWorkspace = computed(() => {
  const id = String(
    route.params.projectId ?? currentSession.value?.projectId ?? projects.activeProjectId ?? '',
  )
  return projects.byId(id) ?? projects.projects[0]
})
const workspaceSessions = computed(() =>
  sessions.sorted.filter(
    (session) =>
      session.projectId === currentWorkspace.value?.id ||
      (!session.projectId && currentWorkspace.value?.id === projects.projects[0]?.id),
  ),
)
const currentTasks = computed(() => {
  const session = currentSession.value
  const runId = session?.runs.at(-1)?.id
  return session && runId ? session.tasks.filter((task) => task.runId === runId) : []
})
const navigation = [
  { label: '工作台', path: '/', icon: Layout },
  { label: 'Agent', path: '/agents', icon: Puzzle },
  { label: '提示词库', path: '/prompts', icon: FileText },
]

async function createConversation() {
  const workspace = currentWorkspace.value
  if (!workspace) {
    await navigateTo('/')
    return
  }
  try {
    const session = await sessions.create('新对话', workspace.id)
    await navigateTo('/sessions/' + session.id)
  } catch (error) {
    message.error(String(error))
  }
}
</script>

<template>
  <aside
    class="sidebar"
    :class="{ collapsed }"
  >
    <NuxtLink
      to="/"
      class="brand-row"
      aria-label="Aurora 首页"
    >
      <span
        class="brand-mark"
        aria-hidden="true"
      />
      <span
        v-if="!collapsed"
        class="brand-name"
      >
        <strong>Aurora</strong>
      </span>
    </NuxtLink>
    <nav
      class="primary-nav"
      aria-label="主导航"
    >
      <button
        class="nav-button"
        aria-label="新对话"
        :title="collapsed ? '新对话' : undefined"
        @click="createConversation"
      >
        <AppIcon
          :component="Edit"
          :size="18"
        />
        <span
          v-if="!collapsed"
          class="nav-label"
        >
          新对话
        </span>
        <AppIcon
          v-if="!collapsed"
          class="nav-end"
          :component="Plus"
          :size="14"
        />
      </button>
      <NuxtLink
        v-for="item in navigation"
        :key="item.path"
        :to="item.path"
        class="nav-button"
        :class="{ active: route.path === item.path }"
        :aria-label="item.label"
        :aria-current="route.path === item.path ? 'page' : undefined"
        :title="collapsed ? item.label : undefined"
      >
        <AppIcon
          :component="item.icon"
          :size="18"
          :weight="route.path === item.path ? 'fill' : 'light'"
        />
        <span
          v-if="!collapsed"
          class="nav-label"
        >
          {{ item.label }}
        </span>
      </NuxtLink>
    </nav>
    <WorkspaceNavigator
      :collapsed="collapsed"
      :workspace="currentWorkspace"
      :sessions="workspaceSessions"
    />
    <TaskTreePanel
      v-if="!collapsed && currentTasks.length"
      :tasks="currentTasks"
      @select="detailTask = $event"
    />
    <nav
      class="sidebar-footer"
      aria-label="应用导航"
    >
      <NuxtLink
        to="/settings"
        class="settings-nav"
        :class="{ active: route.path === '/settings' }"
        aria-label="设置"
        :title="collapsed ? '设置' : undefined"
      >
        <AppIcon
          :component="Settings"
          :size="18"
        />
        <span v-if="!collapsed">设置</span>
      </NuxtLink>
      <div class="runtime-footer"><RuntimeStatus /></div>
    </nav>
    <TaskDetailsDrawer v-model:show="detailTask" />
  </aside>
</template>

<style scoped>
.sidebar {
  display: flex;
  width: 100%;
  height: 100%;
  flex-direction: column;
  overflow: hidden;
}

.brand-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 100px;
  padding: 12px 22px;
}

.brand-mark {
  display: grid;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  place-items: center;
  background: var(--accent);
  mask: url('/logo.svg') center / 29px no-repeat;
  -webkit-mask: url('/logo.svg') center / 29px no-repeat;
}

.brand-name {
  display: grid;
  gap: 1px;
}

.brand-name strong {
  font-size: 16px;
  font-weight: 550;
  letter-spacing: -0.04em;
}

.brand-name span {
  color: var(--text-muted);
  font-size: 9px;
  letter-spacing: 0.12em;
}

.primary-nav {
  display: grid;
  gap: 5px;
  padding: 8px 14px 28px;
}

.nav-button,
.settings-nav {
  position: relative;
  display: flex;
  align-items: center;
  gap: 11px;
  width: 100%;
  min-height: 40px;
  padding: 0 13px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--text-muted);
  text-align: left;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition:
    background 150ms ease,
    color 150ms ease;
}

.nav-button:hover,
.settings-nav:hover {
  color: var(--text);
  background: var(--surface-hover);
}

.nav-button.active,
.settings-nav.active {
  color: var(--accent);
  background: color-mix(in srgb, var(--glass-fill) 55%, transparent);
}

.nav-button.active::before,
.settings-nav.active::before {
  position: absolute;
  left: 0;
  width: 2px;
  height: 14px;
  border-radius: 2px;
  background: var(--accent);
  content: '';
}

.nav-label {
  flex: 1;
}

.nav-end {
  color: var(--text-muted);
}

.sidebar-footer {
  display: grid;
  gap: 12px;
  padding: 18px 14px;
}

.runtime-footer {
  padding: 0 13px 8px;
}

.collapsed .brand-row {
  justify-content: center;
  padding: 0;
}

.collapsed .primary-nav,
.collapsed .sidebar-footer {
  padding-right: 8px;
  padding-left: 8px;
}

.collapsed .nav-button,
.collapsed .settings-nav {
  justify-content: center;
  padding: 0;
}

.collapsed .runtime-footer {
  text-align: center;
  padding: 0;
}

@media (max-width: 600px) {
  .sidebar {
    flex-direction: row;
    align-items: center;
  }

  .brand-row,
  .runtime-footer {
    display: none;
  }

  .primary-nav,
  .collapsed .primary-nav {
    display: flex;
    flex: 1;
    gap: 0;
    padding: 5px;
  }

  .nav-button,
  .settings-nav {
    flex: 1;
    flex-direction: column;
    justify-content: center;
    min-height: 48px;
    gap: 4px;
    padding: 5px 0;
  }

  .nav-button::after,
  .settings-nav::after {
    content: attr(aria-label);
    font-size: 9px;
    line-height: 1;
  }

  .nav-button.active::before,
  .settings-nav.active::before {
    top: 0;
    left: calc(50% - 8px);
    width: 16px;
    height: 2px;
  }

  .sidebar-footer,
  .collapsed .sidebar-footer {
    width: 60px;
    gap: 0;
    padding: 5px 5px 5px 0;
  }
}
</style>
