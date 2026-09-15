<script setup lang="ts">
import { NButton, NIcon, useMessage } from 'naive-ui'
import { FolderPlus } from '@vicons/tabler'
import { isTauri } from '~/utils/runtimeClient'

const sessions = useSessionStore()
const runtime = useRuntimeStore()
const projects = useProjectStore()
const message = useMessage()

onMounted(() => sessions.setActive(null))
useHead({ title: '新对话' })

async function submit(objective: string, attachments: import('~/types/agent').AttachmentRef[]) {
  try {
    const title = objective.replace(/\s+/g, ' ').trim().slice(0, 32)
    const session = await sessions.create(title)
    await navigateTo(`/sessions/${session.id}`)
    await runtime.startRun(session.id, objective, attachments)
  } catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
}

async function addWorkspace() {
  let path: string | null = null
  if (isTauri()) {
    const { open } = await import('@tauri-apps/plugin-dialog')
    const selected = await open({ directory: true, multiple: false })
    path = typeof selected === 'string' ? selected : null
  } else path = window.prompt('输入工作区绝对路径')?.trim() || null
  if (!path) return
  try { await projects.create(path) }
  catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
}
</script>

<template>
  <main class="home-page">
    <section class="welcome">
      <span class="welcome-mark"><img src="/logo.svg" alt="Aurora Agent" ></span>
      <h1>今天要完成什么？</h1>
      <p v-if="projects.projects.length">当前工作区：{{ projects.activeProject?.path || projects.projects[0]?.path }}。Aurora 会规划任务、选择推理强度并在该目录中执行。</p>
      <template v-else>
        <p>先选择一个本机工作区，Aurora 才能建立隔离会话并调用后端工具。</p>
        <NButton type="primary" class="workspace-button" @click="addWorkspace"><template #icon><NIcon :component="FolderPlus" /></template>添加工作区</NButton>
      </template>
    </section>
    <div class="home-composer">
      <MessageComposer
        :disabled="runtime.connectionStatus !== 'connected' || !projects.projects.length"
        :loading="false"
        placeholder="例如：梳理项目架构，并给出下一阶段实施建议"
        @submit="submit"
      />
      <p class="privacy-note">对话与协作记录保存在本机；重启后可以查看历史并继续新一轮任务</p>
    </div>
  </main>
</template>

<style scoped>
.home-page { position: relative; display: grid; width: 100%; height: 100%; place-items: center; padding: 28px 28px 170px; background: transparent; overflow: hidden; }
.welcome { max-width: 560px; text-align: center; }
.welcome-mark { display: grid; width: 52px; height: 52px; margin: 0 auto 20px; place-items: center; border: 1px solid var(--border); border-radius: 15px; background: var(--surface-muted); }
.welcome-mark img { width: 30px; height: 30px; }
.welcome h1 { margin: 0; font-size: clamp(24px, 3vw, 34px); font-weight: 650; letter-spacing: -.035em; }
.welcome p { margin: 13px auto 0; color: var(--text-muted); font-size: 14px; line-height: 1.7; }
.workspace-button { margin-top: 18px; }
.home-composer { position: absolute; right: 28px; bottom: 28px; left: 28px; width: min(860px, calc(100% - 56px)); margin: auto; }
.privacy-note { margin: 8px 0 0; color: var(--text-muted); font-size: 10px; text-align: center; }
@media (max-width: 620px) { .home-page { padding-right: 18px; padding-left: 18px; } .home-composer { right: 18px; left: 18px; width: auto; } }
</style>
