<script setup lang="ts">
import { NBadge, NButton, NIcon, NSpin, NSelect, useMessage } from 'naive-ui'
import { AlertTriangle, Book, Check, Clock, Edit, File, FileDiff, Folder, Loader, Terminal2 } from '@vicons/tabler'
import type { SessionRecord, TaskNode } from '~/types/agent'
import type { WorkflowConfig } from '~/types/workflow'
import { runtimeRequest } from '~/utils/runtimeClient'

const props = defineProps<{ session: SessionRecord }>()
const runtime = useRuntimeStore()
const sessions = useSessionStore()
const notice = useMessage()
const workflows = ref<WorkflowConfig[]>([])
onMounted(async () => {
  try { workflows.value = (await runtimeRequest<{ items: WorkflowConfig[] }>('workflow.list')).items }
  catch (error) { notice.error(String(error)) }
})
async function changeWorkflow(workflowId: string) {
  try { await runtimeRequest('session.update', { sessionId: props.session.id, workflowId }); await sessions.load(props.session.id) }
  catch (error) { notice.error(String(error)) }
}
async function clearContext() {
  try { await runtimeRequest('session.clear', { sessionId: props.session.id }); notice.success('已清空模型上下文，历史记录仍保留') }
  catch (error) { notice.error(String(error)) }
}
const git = useGitStore()
const scrollElement = ref<HTMLElement | null>(null)
const showChanges = ref(false)
const activeRun = computed(() => [...props.session.runs].reverse().find((run) => ['queued', 'running', 'waiting'].includes(run.status)))
const orphanMessages = computed(() => props.session.messages.filter((message) => !message.runId))
const timeline = computed(() => props.session.runs.map((run) => ({
  run,
  user: props.session.messages.find((message) => message.runId === run.id && message.role === 'user'),
  responses: props.session.messages.filter((message) => message.runId === run.id && message.role !== 'user' && message.visibility !== 'internal'),
  tasks: props.session.tasks.filter((task) => task.runId === run.id),
  requests: runtime.approvalsFor(props.session.id).filter((request) => request.runId === run.id),
})))
const statusText = computed(() => ({ idle: '就绪', queued: '排队中', running: '正在执行', waiting: '等待输入', completed: '已完成', failed: '执行失败', cancelled: '已取消', interrupted: '已中断' }[props.session.status]))
const workspaceStatus = computed(() => git.statusFor(props.session.id, 'workspace'))

async function refreshGit() {
  try { await git.loadStatus(props.session.id, 'workspace') } catch { /* 抽屉中展示错误 */ }
}

onMounted(refreshGit)
watch(() => props.session.updatedAt, () => { if (props.session.status === 'completed' || props.session.status === 'failed') refreshGit() })

watch(
  () => [
    props.session.messages.at(-1)?.content.length ?? 0,
    props.session.tasks.map((task) => task.status).join(','),
    runtime.approvalsFor(props.session.id).length,
  ],
  async () => {
    const element = scrollElement.value
    const follow = !element || element.scrollHeight - element.scrollTop - element.clientHeight < 240
    await nextTick()
    if (follow && scrollElement.value) scrollElement.value.scrollTo({ top: scrollElement.value.scrollHeight, behavior: 'smooth' })
  },
)

function statusIcon(task: TaskNode) {
  if (task.status === 'completed') return Check
  if (task.status === 'failed') return AlertTriangle
  if (task.status === 'running') return Loader
  return Clock
}

function activityIcon(task: TaskNode) {
  const tool = task.tool.toLowerCase()
  if (tool.includes('read') || tool.includes('list')) return Book
  if (tool.includes('write') || tool.includes('edit')) return Edit
  if (tool.includes('command') || tool.includes('execute') || tool.includes('run')) return Terminal2
  return statusIcon(task)
}

function activityLabel(task: TaskNode) {
  if (task.status === 'failed') return '执行失败'
  if (task.status === 'waiting') return '等待确认'
  if (task.status === 'running') return '正在执行'
  const tool = task.tool.toLowerCase()
  if (tool.includes('read') || tool.includes('list')) return '已读取文件'
  if (tool.includes('write') || tool.includes('edit')) return '已编辑文件'
  if (tool.includes('command') || tool.includes('execute') || tool.includes('run')) return '已运行命令'
  return '已完成任务'
}
</script>

<template>
  <div class="conversation-view">
    <header class="conversation-header">
      <div class="title-row"><span class="folder-icon"><NIcon :component="Folder" :size="17" /></span><h1>{{ session.title }}</h1></div>
      <div class="header-actions">
        <NSelect :value="session.workflowId" :options="workflows.filter((item) => item.enabled).map((item) => ({ label: item.name, value: item.id }))" :disabled="!!activeRun" size="small" style="width:180px" placeholder="协作流程" @update:value="changeWorkflow" />
        <NButton size="small" quaternary :disabled="!!activeRun" @click="clearContext">清空上下文</NButton>
        <NBadge :value="workspaceStatus?.files.length || 0" :show="!!workspaceStatus?.files.length" :max="99">
          <NButton quaternary size="small" @click="showChanges = true"><template #icon><NIcon :component="FileDiff" /></template>改动</NButton>
        </NBadge>
        <span class="session-state" :class="session.status">{{ statusText }}</span>
      </div>
    </header>

    <main ref="scrollElement" class="conversation-scroll app-scrollbar">
      <div class="conversation-column">
        <NButton v-if="session.nextBeforeSeq" size="small" @click="sessions.loadOlder(session.id)">加载更早消息</NButton>
        <template v-for="message in orphanMessages" :key="message.id">
          <div v-if="message.role === 'user'" class="user-block"><div class="user-message">{{ message.content }}</div></div>
          <article v-else class="assistant-message"><MarkdownContent :content="message.content" /></article>
        </template>

        <section v-for="entry in timeline" :key="entry.run.id" class="run-section">
          <div v-if="entry.user" class="user-block">
            <div class="user-message">{{ entry.user.content }}</div>
            <div v-if="entry.user.attachments.length" class="message-files"><span v-for="file in entry.user.attachments" :key="file.id"><NIcon :component="File" />{{ file.name }}</span></div>
          </div>

          <div v-if="entry.tasks.length" class="activity-feed">
            <div class="activity-row activity-summary">
              <NIcon :component="Book" :size="17" />
              <span>已规划 {{ entry.tasks.length }} 个任务</span>
            </div>
            <div v-for="task in entry.tasks" :key="task.id" :data-task-id="task.id" class="activity-row" :class="task.status">
              <NSpin v-if="task.status === 'running'" :size="15" />
              <NIcon v-else :component="activityIcon(task)" :size="17" />
              <strong>{{ activityLabel(task) }}</strong>
              <span>{{ task.description }}</span>
            </div>
          </div>

          <RunReview :run-id="entry.run.id" :status="entry.run.status" />

          <ApprovalCard v-for="request in entry.requests" :key="request.id" :request="request" @decide="runtime.resolveApproval" @respond="runtime.resumeInput" />

          <template v-for="message in entry.responses" :key="message.id">
            <article v-if="message.kind === 'error' || message.status === 'failed'" class="error-message"><NIcon :component="AlertTriangle" :size="18" /><span>{{ message.content }}</span></article>
            <article v-else-if="message.role === 'tool' || message.kind === 'tool'" class="tool-message"><MarkdownContent :content="message.content" /></article>
            <article v-else class="assistant-message"><MarkdownContent :content="message.content" /><span v-if="message.status === 'streaming'" class="cursor" /></article>
          </template>

          <div v-if="['queued', 'running'].includes(entry.run.status)" class="working-row"><NSpin :size="16" />Aurora 正在处理这个任务</div>
        </section>

        <div v-if="session.status === 'running' && !activeRun" class="working-row"><NSpin :size="16" />Aurora 正在分析并规划任务</div>
      </div>
    </main>
    <GitChangesDrawer v-model:show="showChanges" :session="session" />
  </div>
</template>

<style scoped>
.conversation-view{display:grid;width:100%;height:100%;grid-template-rows:56px minmax(0,1fr);overflow:hidden}.conversation-header{display:flex;align-items:center;justify-content:space-between;padding:0 22px;border-bottom:1px solid var(--border);background:var(--surface)}.title-row{display:flex;min-width:0;align-items:center;gap:9px}.folder-icon{display:grid;width:29px;height:29px;place-items:center;border-radius:8px;background:var(--surface-muted);color:var(--text-muted)}.title-row h1{overflow:hidden;margin:0;font-size:14px;font-weight:650;text-overflow:ellipsis;white-space:nowrap}.header-actions{display:flex;align-items:center;gap:10px}.session-state{padding:3px 8px;border-radius:99px;background:var(--surface-muted);color:var(--text-muted);font-size:11px}.session-state.running,.session-state.queued{color:#2563eb}.session-state.waiting{color:#b7791f}.session-state.completed{color:#16845b}.session-state.failed{color:#c33f3f}
.conversation-scroll{overflow:auto;padding:36px 34px 190px}.conversation-column{width:min(980px,100%);margin:0 auto}.run-section{padding:0 0 31px;margin:0 0 31px;border-bottom:1px solid var(--border)}.run-section:last-of-type{border-bottom:0}.user-block{margin:0 0 30px auto;width:fit-content;max-width:78%}.user-message{padding:10px 15px;border-radius:17px;background:var(--surface-muted);font-size:14px;line-height:1.65}.message-files{display:flex;justify-content:flex-end;flex-wrap:wrap;gap:5px;margin-top:6px}.message-files span{display:flex;align-items:center;gap:4px;color:var(--text-muted);font-size:10px}.message-files svg{width:13px}
.activity-feed{display:grid;gap:15px;margin:4px 0 27px}.activity-row{display:flex;min-height:20px;align-items:center;gap:9px;color:var(--text-muted);font-size:13px;line-height:1.45}.activity-row :deep(svg){flex:0 0 auto}.activity-row strong{color:var(--text-muted);font-weight:600}.activity-row span:last-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.activity-row.failed,.activity-row.failed strong{color:#c33f3f}.activity-row.waiting,.activity-row.waiting strong{color:#b7791f}.activity-summary{margin-bottom:2px}.activity-summary span{font-weight:600}
.assistant-message{position:relative;margin:0 0 25px}.assistant-message :deep(.markdown-content){font-size:15.5px;line-height:1.85}.assistant-message :deep(.markdown-content p){margin-bottom:17px}.cursor{display:inline-block;width:6px;height:15px;margin-left:2px;background:var(--text);animation:blink 1s steps(2) infinite}@keyframes blink{50%{opacity:0}}.error-message{display:flex;gap:9px;margin:10px 0 22px;padding:12px;border:1px solid rgb(209 67 67 / 30%);border-radius:9px;background:rgb(209 67 67 / 7%);color:#c33f3f;font-size:13px}.tool-message{margin:8px 0 20px;padding-left:26px;border-left:2px solid var(--border)}.tool-message :deep(.markdown-content){font-size:13px;color:var(--text-muted)}.working-row{display:flex;align-items:center;gap:9px;margin:18px 0;color:var(--text-muted);font-size:13px}
@media(max-width:680px){.conversation-view{grid-template-rows:auto minmax(0,1fr)}.conversation-header{flex-wrap:wrap;gap:8px;padding:10px 14px}.header-actions{flex-wrap:wrap}.title-row{width:100%}.conversation-scroll{padding:26px 16px 176px}.conversation-column{width:100%}.user-block{max-width:92%}.activity-row span:last-child{white-space:normal}.assistant-message :deep(.markdown-content){font-size:14.5px}.run-section{margin-bottom:24px;padding-bottom:24px}}
</style>
