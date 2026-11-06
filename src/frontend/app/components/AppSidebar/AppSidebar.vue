<script setup lang="ts">
import { useMessage } from 'naive-ui'
import { Edit, FileText, Folder, Layout, Puzzle, Settings } from '~/utils/icons'
import type { ProjectRecord } from '~/types/agent'

const props = defineProps<{ workspace?: ProjectRecord; workspaceOpen: boolean }>()
const emit = defineEmits<{ workspace: [] }>()
const route = useRoute()
const sessions = useSessionStore()
const message = useMessage()
const navigation = [
  { label: '工作台', path: '/', icon: Layout },
  { label: 'Agent', path: '/agents', icon: Puzzle },
  { label: '提示词库', path: '/prompts', icon: FileText },
]

async function createConversation() {
  if (!props.workspace) {
    await navigateTo('/')
    return
  }
  try {
    const session = await sessions.create('新对话', props.workspace.id)
    await navigateTo('/sessions/' + session.id)
  } catch (error) {
    message.error(String(error))
  }
}
</script>

<template>
  <aside class="navigation-rail">
    <NuxtLink
      to="/"
      class="rail-brand"
      aria-label="Aurora 首页"
    >
      <span />
    </NuxtLink>
    <nav
      class="rail-navigation"
      aria-label="主导航"
    >
      <button
        class="rail-button"
        aria-label="新对话"
        @click="createConversation"
      >
        <AppIcon
          :component="Edit"
          :size="21"
        />
        <span>新对话</span>
      </button>
      <NuxtLink
        v-for="item in navigation"
        :key="item.path"
        :to="item.path"
        class="rail-button"
        :class="{ active: route.path === item.path }"
        :aria-label="item.label"
        :aria-current="route.path === item.path ? 'page' : undefined"
      >
        <AppIcon
          :component="item.icon"
          :size="21"
          :weight="route.path === item.path ? 'fill' : 'regular'"
        />
        <span>{{ item.label }}</span>
      </NuxtLink>
      <button
        class="rail-button workspace-rail-button"
        :class="{ 'panel-open': workspaceOpen }"
        aria-label="工作区"
        :aria-expanded="workspaceOpen"
        aria-controls="workspace-panel"
        @click="emit('workspace')"
      >
        <AppIcon
          :component="Folder"
          :size="21"
          :weight="workspaceOpen ? 'fill' : 'regular'"
        />
        <span>工作区</span>
      </button>
    </nav>
    <nav
      class="rail-footer"
      aria-label="应用导航"
    >
      <NuxtLink
        to="/settings"
        class="rail-button"
        :class="{ active: route.path === '/settings' }"
        aria-label="设置"
        :aria-current="route.path === '/settings' ? 'page' : undefined"
      >
        <AppIcon
          :component="Settings"
          :size="21"
          :weight="route.path === '/settings' ? 'fill' : 'regular'"
        />
        <span>设置</span>
      </NuxtLink>
      <div class="rail-runtime"><RuntimeStatus /></div>
    </nav>
  </aside>
</template>

<style scoped>
.navigation-rail {
  display: flex;
  width: 84px;
  height: 100%;
  flex-direction: column;
  align-items: center;
  padding: 0 10px 20px;
}

.rail-brand {
  display: grid;
  width: 64px;
  height: 96px;
  flex: 0 0 96px;
  place-items: center;
}

.rail-brand span {
  width: 30px;
  height: 34px;
  background: var(--accent);
  mask: url('/logo.svg') center / contain no-repeat;
  -webkit-mask: url('/logo.svg') center / contain no-repeat;
}

.rail-navigation,
.rail-footer {
  display: grid;
  width: 100%;
  gap: 8px;
}

.rail-button {
  position: relative;
  display: flex;
  width: 64px;
  min-height: 58px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 8px 2px;
  border: 1px solid transparent;
  border-radius: 19px;
  background: transparent;
  color: var(--text-muted);
  cursor: pointer;
  transition:
    color 160ms ease,
    background 160ms ease,
    transform 160ms ease;
}

.rail-button > span {
  font-size: 10px;
  font-weight: 500;
  line-height: 1;
  white-space: nowrap;
}

.rail-button:hover {
  background: var(--glass-fill);
  color: var(--text);
  transform: translateY(-1px);
}

.rail-button.active {
  border-color: var(--glass-edge);
  background: var(--glass-fill);
  color: var(--accent);
  box-shadow: 0 3px 12px color-mix(in srgb, var(--accent-source) 5%, transparent);
}

.workspace-rail-button.panel-open {
  color: var(--accent);
}

.workspace-rail-button.panel-open::after {
  position: absolute;
  right: -10px;
  width: 3px;
  height: 16px;
  border-radius: 3px;
  background: var(--accent);
  content: '';
}

.rail-footer {
  margin-top: auto;
  padding-top: 24px;
}

.rail-runtime {
  text-align: center;
}

.rail-runtime :deep(.status-label) {
  display: none;
}

@media (max-width: 600px) {
  .navigation-rail {
    width: 100%;
    height: 100%;
    flex-direction: row;
    padding: 4px 8px;
  }

  .rail-brand,
  .rail-runtime {
    display: none;
  }

  .rail-navigation {
    display: flex;
    flex: 1;
    gap: 2px;
  }

  .rail-footer {
    width: auto;
    margin: 0;
    padding: 0;
  }

  .rail-button {
    width: 100%;
    min-width: 0;
    min-height: 52px;
    flex: 1;
    gap: 5px;
    border-radius: 14px;
  }

  .rail-button > span {
    font-size: 9px;
  }

  .rail-footer .rail-button {
    width: 48px;
  }

  .workspace-rail-button.panel-open::after {
    top: 0;
    right: calc(50% - 8px);
    width: 16px;
    height: 2px;
  }
}

@media (max-height: 650px) and (min-width: 601px) {
  .rail-brand {
    height: 64px;
    flex-basis: 64px;
  }

  .rail-button {
    min-height: 50px;
  }

  .rail-navigation,
  .rail-footer {
    gap: 3px;
  }
}
</style>
