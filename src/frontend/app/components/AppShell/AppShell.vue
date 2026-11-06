<script setup lang="ts">
import { ChevronLeft, ChevronRight } from '~/utils/icons'

const ui = useUiStore()
const route = useRoute()
const compact = ref(false)
const collapsed = computed(() => compact.value || ui.sidebarCollapsed)
const isCanvas = computed(() => route.path === '/' || route.path.startsWith('/projects/'))
let compactQuery: MediaQueryList | null = null

function syncCompact(event?: MediaQueryListEvent) {
  compact.value = event?.matches ?? compactQuery?.matches ?? false
}

onMounted(() => {
  compactQuery = window.matchMedia('(max-width: 860px)')
  syncCompact()
  compactQuery.addEventListener('change', syncCompact)
})
onBeforeUnmount(() => compactQuery?.removeEventListener('change', syncCompact))
</script>

<template>
  <div
    class="app-shell"
    :class="{ 'is-collapsed': collapsed, 'is-canvas': isCanvas }"
  >
    <CanvasAtmosphere />
    <div class="app-sider">
      <AppSidebar :collapsed="collapsed" />
      <button
        v-if="!compact"
        class="sidebar-toggle"
        :aria-label="collapsed ? '展开菜单' : '收起菜单'"
        :aria-expanded="!collapsed"
        @click="ui.toggleSidebar"
      >
        <AppIcon
          :component="collapsed ? ChevronRight : ChevronLeft"
          :size="14"
        />
      </button>
    </div>
    <div class="app-content"><slot /></div>
  </div>
</template>

<style scoped>
.app-shell {
  position: relative;
  display: grid;
  grid-template-columns: 224px minmax(0, 1fr);
  width: 100%;
  height: 100vh;
  height: 100dvh;
  isolation: isolate;
  background: var(--app-bg);
  overflow: hidden;
}

.app-shell.is-collapsed {
  grid-template-columns: 72px minmax(0, 1fr);
}

.app-shell > :deep(.canvas-atmosphere) {
  opacity: 0.38;
  transition: opacity 300ms ease;
}

.app-shell.is-canvas > :deep(.canvas-atmosphere) {
  opacity: 1;
}

.app-sider {
  position: relative;
  z-index: 2;
  min-height: 0;
  background: var(--sidebar-glass);
  -webkit-backdrop-filter: blur(var(--material-blur)) saturate(135%);
  backdrop-filter: blur(var(--material-blur)) saturate(135%);
}

.app-sider::after {
  position: absolute;
  top: 0;
  right: 0;
  bottom: 0;
  width: 1px;
  background: linear-gradient(
    transparent,
    var(--glass-edge) 18%,
    var(--glass-edge) 82%,
    transparent
  );
  content: '';
  pointer-events: none;
}

.sidebar-toggle {
  position: absolute;
  top: 37px;
  right: 12px;
  display: grid;
  width: 26px;
  height: 26px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition: background 150ms ease;
}

.sidebar-toggle:hover {
  background: var(--surface-hover);
}

.is-collapsed .sidebar-toggle {
  top: 90px;
  right: 23px;
}

.is-collapsed .app-sider :deep(.primary-nav) {
  padding-top: 44px;
}

.app-content {
  position: relative;
  z-index: 1;
  min-width: 0;
  min-height: 0;
  background: transparent;
  overflow: hidden;
}

@media (max-width: 860px) {
  .is-collapsed .app-sider :deep(.primary-nav) {
    padding-top: 8px;
  }
}

@media (max-width: 600px) {
  .app-shell,
  .app-shell.is-collapsed {
    grid-template-columns: minmax(0, 1fr);
    grid-template-rows: minmax(0, 1fr) calc(64px + env(safe-area-inset-bottom));
  }

  .app-sider {
    grid-row: 2;
    padding-bottom: env(safe-area-inset-bottom);
    box-shadow: 0 -1px 0 var(--glass-edge);
  }

  .app-sider::after {
    display: none;
  }

  .app-content {
    grid-row: 1;
  }
}
</style>
