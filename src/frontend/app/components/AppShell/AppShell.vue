<script setup lang="ts">
import { NLayout, NLayoutContent, NLayoutSider } from "naive-ui";

const ui = useUiStore();
const compact = ref(false);
const collapsed = computed(() => compact.value || ui.sidebarCollapsed);
const edgeLeft = computed(() => `${8 + (collapsed.value ? 68 : 280) - 6}px`);
let compactQuery: MediaQueryList | null = null;

function syncCompact(event?: MediaQueryListEvent) {
  compact.value = event?.matches ?? compactQuery?.matches ?? false;
}

onMounted(() => {
  compactQuery = window.matchMedia("(max-width: 860px)");
  syncCompact();
  compactQuery.addEventListener("change", syncCompact);
});

onBeforeUnmount(() => compactQuery?.removeEventListener("change", syncCompact));
</script>

<template>
  <NLayout class="app-shell" has-sider>
    <NLayoutSider
      class="app-sider"
      :width="collapsed ? 68 : 280"
      :native-scrollbar="false"
      content-style="height: 100%;"
      bordered
    >
      <AppSidebar :collapsed="collapsed" />
    </NLayoutSider>
    <div
      v-if="!compact"
      class="sidebar-edge"
      :style="{ left: edgeLeft }"
      role="button"
      tabindex="0"
      :aria-label="collapsed ? '展开菜单' : '收起菜单'"
      :aria-expanded="!collapsed"
      @click="ui.toggleSidebar"
      @keydown.enter.prevent="ui.toggleSidebar"
      @keydown.space.prevent="ui.toggleSidebar"
    >
      <span />
    </div>
    <NLayoutContent
      class="app-content"
      content-style="height: 100%; overflow: hidden;"
    >
      <slot />
    </NLayoutContent>
  </NLayout>
</template>

<style scoped>
.app-shell {
  position: relative;
  width: 100vw;
  height: 100vh;
  height: 100dvh;
  padding: 8px;
  gap: 8px;
  background: var(--shell-bg);
  overflow: hidden;
}

.sidebar-edge {
  position: absolute;
  top: 16px;
  bottom: 16px;
  z-index: 10;
  display: grid;
  width: 12px;
  place-items: center;
  cursor: pointer;
  outline: none;
}

.sidebar-edge span {
  width: 1px;
  height: 42px;
  border-radius: 1px;
  background: transparent;
  transition:
    background 120ms ease,
    height 120ms ease;
}
.sidebar-edge:hover span,
.sidebar-edge:focus-visible span {
  height: 64px;
  background: var(--text-muted);
}

.app-sider {
  height: calc(100vh - 16px);
  height: calc(100dvh - 16px);
  border: 1px solid var(--glass-border) !important;
  border-radius: 16px;
  background: var(--sidebar-glass) !important;
  box-shadow: var(--glass-shadow);
  backdrop-filter: blur(22px) saturate(125%);
  -webkit-backdrop-filter: blur(22px) saturate(125%);
  overflow: hidden;
}

.app-sider :deep(.n-layout-sider-scroll-container) {
  height: 100%;
  background: transparent;
}

.app-content {
  min-width: 0;
  margin-left: 8px;
  height: calc(100vh - 16px);
  height: calc(100dvh - 16px);
  border: 1px solid var(--glass-border);
  border-radius: 16px;
  background: var(--content-glass);
  box-shadow: var(--glass-shadow-soft);
  overflow: hidden;
}

@media (max-width: 680px) {
  .app-shell {
    padding: 5px;
    gap: 5px;
  }
  .app-content {
    margin-left: 5px;
  }
  .app-sider,
  .app-content {
    height: calc(100dvh - 10px);
    border-radius: 13px;
  }
}
</style>
