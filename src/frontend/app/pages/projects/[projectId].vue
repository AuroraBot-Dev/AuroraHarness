<script setup lang="ts">
import { NButton, NEmpty, NIcon, NSpin, useMessage } from 'naive-ui'
import { Folder } from '@vicons/tabler'
import type { AttachmentRef } from '~/types/agent'

const route = useRoute()
const projects = useProjectStore()
const sessions = useSessionStore()
const runtime = useRuntimeStore()
const message = useMessage()
const projectId = computed(() => String(route.params.projectId ?? ''))
const project = computed(() => projects.byId(projectId.value))
const loading = ref(false)

async function loadProject(id: string) {
  projects.setActive(id)
  loading.value = true
  try {
    await projects.get(id)
  } catch { projects.remove(id) } finally { loading.value = false }
}
watch(projectId, loadProject, { immediate: true })
useHead({ title: computed(() => project.value?.name ?? '项目') })

async function submit(objective: string, attachments: AttachmentRef[]) {
  try {
    const title = objective.replace(/\s+/g, ' ').trim().slice(0, 32)
    const session = await sessions.create(title, projectId.value)
    await navigateTo(`/sessions/${session.id}`)
    await runtime.startRun(session.id, objective, attachments)
  } catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
}
</script>

<template>
  <main class="project-page">
    <div v-if="loading" class="center-state"><NSpin size="large" /></div>
    <template v-else-if="project">
      <section class="project-welcome">
        <span class="project-mark"><NIcon :component="Folder" :size="24" /></span>
        <h1>{{ project.name }}</h1>
        <p>在此项目中发起新对话；项目内对话已显示在左侧菜单。</p>
      </section>
      <div class="project-composer">
        <MessageComposer :disabled="runtime.connectionStatus !== 'connected'" placeholder="在「{{ project.name }}」中描述目标…" @submit="submit" />
      </div>
    </template>
    <div v-else class="center-state"><NEmpty description="没有找到这个项目"><template #extra><NButton @click="navigateTo('/')">返回首页</NButton></template></NEmpty></div>
  </main>
</template>

<style scoped>
.project-page { position: relative; display: grid; width: 100%; height: 100%; place-items: center; padding: 28px 28px 170px; overflow: hidden; }
.center-state { display: grid; height: 100%; place-items: center; }
.project-welcome { max-width: 560px; text-align: center; }
.project-mark { display: grid; width: 52px; height: 52px; margin: 0 auto 18px; place-items: center; border: 1px solid var(--border); border-radius: 15px; background: var(--surface-muted); }
.project-welcome h1 { margin: 0; font-size: clamp(22px, 3vw, 30px); font-weight: 650; letter-spacing: -.03em; }
.project-welcome p { margin: 12px auto 0; color: var(--text-muted); font-size: 13px; }
.project-composer { position: absolute; right: 28px; bottom: 28px; left: 28px; width: min(860px, calc(100% - 56px)); margin: auto; }
</style>
