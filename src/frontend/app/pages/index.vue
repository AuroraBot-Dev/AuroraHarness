<script setup lang="ts">
import { NButton, useMessage } from 'naive-ui'
import { Folder, FolderPlus } from '~/utils/icons'
import type { AttachmentRef } from '~/types/agent'
import { isTauri } from '~/utils/runtimeClient'

const sessions = useSessionStore()
const runtime = useRuntimeStore()
const projects = useProjectStore()
const prompts = usePromptStore()
const message = useMessage()
const starting = ref(false)
const workspace = computed(() => projects.activeProject ?? projects.projects[0])

onMounted(() => {
  sessions.setActive(null)
  prompts.hydrate()
})
useHead({ title: '工作台' })

async function submit(objective: string, attachments: AttachmentRef[]) {
  starting.value = true
  try {
    const title = objective.replace(/\s+/g, ' ').trim().slice(0, 32)
    const session = await sessions.create(title)
    await navigateTo(`/sessions/${session.id}`)
    await runtime.startRun(session.id, objective, attachments)
  } catch (error) {
    prompts.draft = objective
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    starting.value = false
  }
}

async function addWorkspace() {
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
    await projects.create(path)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  }
}
</script>

<template>
  <main class="home-page app-scrollbar">
    <header class="home-header">
      <span>工作台</span>
      <NButton
        quaternary
        size="small"
        :disabled="runtime.connectionStatus !== 'connected'"
        @click="addWorkspace"
      >
        <template #icon>
          <AppIcon
            :component="FolderPlus"
            :size="16"
          />
        </template>
        添加工作区
      </NButton>
    </header>
    <div class="home-body">
      <section class="welcome">
        <h1>
          <span>今天，</span>
          <span>想做点什么？</span>
        </h1>
        <p>把下一步，留给你的 Agent。</p>
      </section>
      <section
        class="home-compose"
        aria-label="新任务"
      >
        <MessageComposer
          v-model="prompts.draft"
          :disabled="runtime.connectionStatus !== 'connected' || !workspace"
          :loading="starting"
          :placeholder="workspace ? '描述你想完成的事…' : '先添加工作区，再描述你的想法…'"
          @submit="submit"
        >
          <template #context>
            <div class="workspace-bar">
              <AppIcon
                :component="Folder"
                :size="15"
              />
              <span>{{ workspace?.name || '添加一个工作区，即可开始' }}</span>
            </div>
          </template>
        </MessageComposer>
      </section>
      <p class="compose-note">
        {{
          runtime.connectionStatus !== 'connected'
            ? '正在等待本机运行时连接'
            : '在你的工作区中执行，记录保存在本机。'
        }}
      </p>
    </div>
  </main>
</template>

<style scoped>
.home-page {
  display: flex;
  width: 100%;
  height: 100%;
  flex-direction: column;
  overflow-y: auto;
}

.home-header {
  display: flex;
  flex: 0 0 88px;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 clamp(28px, 5vw, 72px);
}

.home-header > span {
  color: var(--text-muted);
  font-size: 11px;
  letter-spacing: 0.06em;
}

.home-body {
  display: flex;
  width: min(1040px, 100%);
  flex: 1;
  flex-direction: column;
  justify-content: center;
  margin: 0 auto;
  padding: 32px clamp(28px, 5vw, 72px) 100px;
}

.welcome {
  margin-bottom: 52px;
}

.welcome h1 {
  margin: 0;
  font-size: clamp(38px, 4.5vw, 70px);
  font-weight: 450;
  letter-spacing: -0.06em;
  line-height: 1.22;
}

.welcome h1 span {
  display: block;
}

.welcome h1 span:first-child {
  color: var(--text-muted);
}

.welcome p {
  margin: 22px 0 0;
  color: var(--text-muted);
  font-size: 13px;
  letter-spacing: 0.02em;
}

.home-compose {
  width: calc(100% - 40px);
  margin-left: 40px;
}

.workspace-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-muted);
  font-size: 11px;
}

.workspace-bar span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.home-compose :deep(.composer-input textarea) {
  min-height: 82px;
}

.compose-note {
  margin: 18px 0 0 40px;
  color: var(--text-muted);
  font-size: 10px;
  text-align: center;
}

@media (max-width: 680px) {
  .home-header {
    flex-basis: 64px;
    padding: 0 24px;
  }

  .home-body {
    padding: 28px 24px 64px;
  }

  .welcome {
    margin-bottom: 36px;
  }

  .welcome h1 {
    font-size: clamp(34px, 8vw, 48px);
  }

  .home-compose {
    width: 100%;
    margin-left: 0;
  }

  .compose-note {
    margin-left: 0;
    font-size: 9px;
  }
}

@media (max-height: 580px) {
  .home-body {
    flex: 0 0 auto;
    padding-top: 16px;
    padding-bottom: 32px;
  }

  .welcome {
    margin-bottom: 24px;
  }
}
</style>
