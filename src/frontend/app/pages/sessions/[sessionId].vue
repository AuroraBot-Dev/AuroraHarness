<script setup lang="ts">
import { NAlert, NButton, NEmpty, NSpin, useMessage } from 'naive-ui'
import type { AttachmentRef } from '~/types/agent'

const route = useRoute()
const sessions = useSessionStore()
const runtime = useRuntimeStore()
const message = useMessage()
const sessionId = computed(() => String(route.params.sessionId ?? ''))
const session = computed(() => sessions.getSession(sessionId.value))
const loading = ref(false)
const notFound = ref(false)
const running = computed(() => !!session.value && sessions.isRunning(session.value.id))

async function loadSession(id: string) {
  sessions.setActive(id); notFound.value = false
  loading.value = true
  try { await sessions.load(id) } catch { notFound.value = true } finally { loading.value = false }
}
watch(sessionId, loadSession, { immediate: true })
useHead({ title: computed(() => session.value?.title ?? '会话') })

async function submit(objective: string, attachments: AttachmentRef[]) {
  if (!session.value) return
  try { await runtime.startRun(session.value.id, objective, attachments) }
  catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
}
</script>

<template>
  <main class="session-page">
    <div v-if="loading || (!sessions.loaded && !notFound)" class="center-state"><NSpin size="large" /></div>
    <template v-else-if="session">
      <ConversationView :session="session" />
      <div class="session-composer">
        <NAlert v-if="runtime.connectionStatus === 'disconnected'" type="warning" :show-icon="false" class="disconnect-alert">运行时已断开。历史仍可浏览，恢复连接后可继续对话。</NAlert>
        <MessageComposer :disabled="runtime.connectionStatus !== 'connected'" :loading="running" placeholder="继续这个会话…" @submit="submit" />
      </div>
    </template>
    <div v-else class="center-state"><NEmpty description="没有找到这个会话"><template #extra><NButton @click="navigateTo('/')">返回新对话</NButton></template></NEmpty></div>
  </main>
</template>

<style scoped>
.session-page{position:relative;width:100%;height:100%;overflow:hidden}.center-state{display:grid;height:100%;place-items:center}.session-composer{position:absolute;right:24px;bottom:20px;left:24px;z-index:4;width:min(820px,calc(100% - 48px));margin:auto}.disconnect-alert{margin-bottom:7px;border-radius:8px;font-size:11px}@media(max-width:620px){.session-composer{right:14px;bottom:14px;left:14px;width:auto}}
</style>
