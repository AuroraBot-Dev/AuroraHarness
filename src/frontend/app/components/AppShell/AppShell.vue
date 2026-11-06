<script setup lang="ts">
import { NDrawer } from 'naive-ui'
import type { TaskNode } from '~/types/agent'

const ui = useUiStore()
const route = useRoute()
const projects = useProjectStore()
const sessions = useSessionStore()
const compact = ref(false)
const mobile = ref(false)
const overlayOpen = ref(false)
const detailTask = ref<TaskNode | null>(null)
const currentSession = computed(() => sessions.getSession(String(route.params.sessionId ?? '')))
const workspace = computed(() => {
  const id = String(
    route.params.projectId ?? currentSession.value?.projectId ?? projects.activeProjectId ?? '',
  )
  return projects.byId(id) ?? projects.projects[0]
})
const workspaceSessions = computed(() => {
  if (!workspace.value) return []
  return sessions.sorted.filter(
    (session) =>
      session.projectId === workspace.value?.id ||
      (!session.projectId && workspace.value?.id === projects.projects[0]?.id),
  )
})
const tasks = computed(() => {
  const session = currentSession.value
  const runId = session?.runs.at(-1)?.id
  return session && runId ? session.tasks.filter((task) => task.runId === runId) : []
})
const workspaceOpen = computed(() => (compact.value ? overlayOpen.value : !ui.sidebarCollapsed))
const isCanvas = computed(() => route.path === '/' || route.path.startsWith('/projects/'))
let compactQuery: MediaQueryList | null = null
let mobileQuery: MediaQueryList | null = null

function syncViewport() {
  compact.value = compactQuery?.matches ?? false
  mobile.value = mobileQuery?.matches ?? false
  if (!compact.value) overlayOpen.value = false
}

function toggleWorkspace() {
  if (compact.value) overlayOpen.value = !overlayOpen.value
  else ui.toggleSidebar()
}

function closeWorkspace() {
  if (compact.value) overlayOpen.value = false
  else if (!ui.sidebarCollapsed) ui.toggleSidebar()
}

onMounted(() => {
  compactQuery = window.matchMedia('(max-width: 1024px)')
  mobileQuery = window.matchMedia('(max-width: 600px)')
  syncViewport()
  compactQuery.addEventListener('change', syncViewport)
  mobileQuery.addEventListener('change', syncViewport)
})
onBeforeUnmount(() => {
  compactQuery?.removeEventListener('change', syncViewport)
  mobileQuery?.removeEventListener('change', syncViewport)
})
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'panel-visible': !compact && workspaceOpen, 'is-canvas': isCanvas }"
  >
    <CanvasAtmosphere />
    <div class="app-rail">
      <AppSidebar
        :workspace="workspace"
        :workspace-open="workspaceOpen"
        @workspace="toggleWorkspace"
      />
    </div>
    <div
      v-if="!compact && workspaceOpen"
      id="workspace-panel"
      class="app-workspace"
    >
      <WorkspaceNavigator
        :workspace="workspace"
        :sessions="workspaceSessions"
        :tasks="tasks"
        @close="closeWorkspace"
        @select-task="detailTask = $event"
      />
    </div>
    <div class="app-content"><slot /></div>
    <NDrawer
      v-if="compact"
      v-model:show="overlayOpen"
      :placement="mobile ? 'bottom' : 'left'"
      :width="mobile ? undefined : 320"
      :height="mobile ? '82dvh' : undefined"
      :auto-focus="false"
      :style="{
        maxWidth: '100vw',
        background: 'var(--app-bg)',
        borderRadius: mobile ? '24px 24px 0 0' : '0 24px 24px 0',
      }"
    >
      <div
        id="workspace-panel"
        class="workspace-overlay"
      >
        <WorkspaceNavigator
          :workspace="workspace"
          :sessions="workspaceSessions"
          :tasks="tasks"
          @close="closeWorkspace"
          @navigate="overlayOpen = false"
          @select-task="detailTask = $event"
        />
      </div>
    </NDrawer>
    <TaskDetailsDrawer v-model:show="detailTask" />
  </div>
</template>

<style scoped>
.app-shell {
  position: relative;
  display: grid;
  grid-template-columns: 84px minmax(0, 1fr);
  width: 100%;
  height: 100vh;
  height: 100dvh;
  isolation: isolate;
  background: var(--app-bg);
  overflow: hidden;
}

.app-shell.panel-visible {
  grid-template-columns: 84px 232px minmax(0, 1fr);
}

.app-shell > :deep(.canvas-atmosphere) {
  opacity: 0.38;
  transition: opacity 300ms ease;
}

.app-shell.is-canvas > :deep(.canvas-atmosphere) {
  opacity: 1;
}

.app-rail,
.app-workspace {
  position: relative;
  z-index: 2;
  min-height: 0;
  background: var(--sidebar-glass);
  -webkit-backdrop-filter: blur(var(--material-blur)) saturate(135%);
  backdrop-filter: blur(var(--material-blur)) saturate(135%);
}

.app-workspace {
  box-shadow:
    inset 1px 0 0 var(--glass-edge),
    1px 0 0 var(--glass-edge);
}

.app-content {
  position: relative;
  z-index: 1;
  min-width: 0;
  min-height: 0;
  background: transparent;
  overflow: hidden;
}

.workspace-overlay {
  height: 100%;
  overflow: hidden;
}

@media (max-width: 1024px) {
  .app-shell.panel-visible {
    grid-template-columns: 84px minmax(0, 1fr);
  }

  .app-workspace {
    display: none;
  }
}

@media (max-width: 600px) {
  .app-shell,
  .app-shell.panel-visible {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) calc(64px + env(safe-area-inset-bottom));
  }

  .app-rail {
    grid-row: 2;
    padding-bottom: env(safe-area-inset-bottom);
    box-shadow: 0 -1px 0 var(--glass-edge);
  }

  .app-content {
    grid-row: 1;
  }
}
</style>
