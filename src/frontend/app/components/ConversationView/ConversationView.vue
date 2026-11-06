<script setup lang="ts">
import { NButton, NDropdown, NSpin, useMessage } from 'naive-ui'
import { AlertTriangle, Dots, File } from '~/utils/icons'
import type { SessionRecord } from '~/types/agent'
import type { WorkflowConfig } from '~/types/workflow'
import { runtimeRequest } from '~/utils/runtimeClient'

const props = defineProps<{ session: SessionRecord }>()
const view = defineModel<'conversation' | 'trace'>('view', { default: 'conversation' })
const traceRunId = ref('')
const route = useRoute()
const runtime = useRuntimeStore()
const sessions = useSessionStore()
const notice = useMessage()
const workflows = ref<WorkflowConfig[]>([])
onMounted(async () => {
  try {
    workflows.value = (await runtimeRequest<{ items: WorkflowConfig[] }>('workflow.list')).items
  } catch (error) {
    notice.error(String(error))
  }
})
async function changeWorkflow(workflowId: string) {
  try {
    await runtimeRequest('session.update', { sessionId: props.session.id, workflowId })
    await sessions.load(props.session.id)
  } catch (error) {
    notice.error(String(error))
  }
}
async function clearContext() {
  try {
    await runtimeRequest('session.clear', { sessionId: props.session.id })
    notice.success('已清空模型上下文，历史记录仍保留')
  } catch (error) {
    notice.error(String(error))
  }
}
const git = useGitStore()
const scrollElement = ref<HTMLElement | null>(null)
const showChanges = ref(false)
const optionsOpen = ref(false)
const activeRun = computed(() =>
  [...props.session.runs]
    .reverse()
    .find((run) => ['queued', 'running', 'waiting'].includes(run.status)),
)
const orphanMessages = computed(() => props.session.messages.filter((message) => !message.runId))
const timeline = computed(() =>
  props.session.runs.map((run) => ({
    run,
    user: props.session.messages.find(
      (message) => message.runId === run.id && message.role === 'user',
    ),
    responses: props.session.messages.filter(
      (message) =>
        message.runId === run.id && message.role !== 'user' && message.visibility !== 'internal',
    ),
    requests: runtime.requestsFor(props.session.id).filter((request) => request.runId === run.id),
  })),
)
const sessionOptions = computed(() => [
  {
    label: '协作流程',
    key: 'workflow',
    disabled:
      !!activeRun.value ||
      runtime.connectionStatus !== 'connected' ||
      !workflows.value.some((item) => item.enabled),
    children: workflows.value
      .filter((item) => item.enabled)
      .map((item) => ({
        label: item.name + (item.id === props.session.workflowId ? '（当前）' : ''),
        key: `workflow:${item.id}`,
        disabled: !!activeRun.value || runtime.connectionStatus !== 'connected',
      })),
  },
  {
    label: '清空上下文',
    key: 'clear',
    disabled: !!activeRun.value || runtime.connectionStatus !== 'connected',
  },
])

function selectSessionOption(key: string | number) {
  if (key === 'clear') void clearContext()
  else if (String(key).startsWith('workflow:'))
    void changeWorkflow(String(key).slice('workflow:'.length))
}
const pendingRequests = computed(() =>
  runtime.requestsFor(props.session.id).filter((request) => request.status === 'pending'),
)
async function stopTraceRun() {
  try {
    await runtime.cancelRun(props.session.id)
  } catch (error) {
    notice.error(error instanceof Error ? error.message : String(error))
  }
}

function openTrace(runId: string) {
  traceRunId.value = runId
  view.value = 'trace'
}

function switchTab(event: KeyboardEvent) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  view.value =
    event.key === 'Home'
      ? 'conversation'
      : event.key === 'End'
        ? 'trace'
        : view.value === 'conversation'
          ? 'trace'
          : 'conversation'
  nextTick(() => document.getElementById(`session-tab-${view.value}`)?.focus())
}

async function refreshGit() {
  try {
    await git.loadStatus(props.session.id, 'workspace')
  } catch {
    /* 抽屉中展示错误 */
  }
}

onMounted(async () => {
  void refreshGit()
  await nextTick()
  if (!route.hash && scrollElement.value)
    scrollElement.value.scrollTo({ top: scrollElement.value.scrollHeight, behavior: 'instant' })
})
watch(
  () => props.session.updatedAt,
  () => {
    if (props.session.status === 'completed' || props.session.status === 'failed') refreshGit()
  },
)

watch(
  () => [
    props.session.messages.at(-1)?.content.length ?? 0,
    props.session.tasks.map((task) => task.status).join(','),
    runtime.approvalsFor(props.session.id).length,
  ],
  async () => {
    if (view.value !== 'conversation') return
    const element = scrollElement.value
    const follow = !element || element.scrollHeight - element.scrollTop - element.clientHeight < 240
    await nextTick()
    if (follow && scrollElement.value)
      scrollElement.value.scrollTo({ top: scrollElement.value.scrollHeight, behavior: 'instant' })
  },
)

async function respondToInput(id: string, response: unknown) {
  try {
    await runtime.resumeInput(id, response)
  } catch (error) {
    notice.error(error instanceof Error ? error.message : String(error))
  }
}

async function decideApproval(id: string, approved: boolean) {
  try {
    await runtime.resolveApproval(id, approved)
  } catch (error) {
    notice.error(error instanceof Error ? error.message : String(error))
  }
}

async function retryRun(runId: string) {
  try {
    await runtime.retryRun(props.session.id, runId)
  } catch (error) {
    notice.error(error instanceof Error ? error.message : String(error))
  }
}

async function showRetrySource(runId: string) {
  view.value = 'conversation'
  await nextTick()
  document.getElementById(`run-${runId}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}
</script>

<template>
  <div class="conversation-view">
    <header class="conversation-header">
      <div class="header-top">
        <div class="title-row">
          <h1>{{ session.title }}</h1>
        </div>
        <div class="header-actions">
          <NButton
            size="small"
            quaternary
            @click="showChanges = true"
          >
            改动
          </NButton>
          <NDropdown
            v-model:show="optionsOpen"
            trigger="click"
            placement="bottom-end"
            :options="sessionOptions"
            :node-props="
              (option) => ({
                role: 'menuitem',
                'aria-disabled': option.disabled ? 'true' : 'false',
                'aria-haspopup': option.children?.length ? 'menu' : undefined,
              })
            "
            @select="selectSessionOption"
          >
            <NButton
              quaternary
              circle
              size="small"
              aria-label="会话选项"
              aria-haspopup="menu"
              :aria-expanded="optionsOpen"
            >
              <template #icon><AppIcon :component="Dots" /></template>
            </NButton>
          </NDropdown>
        </div>
      </div>
      <div class="view-bar">
        <div
          class="view-tabs"
          role="tablist"
          aria-label="会话视图"
          @keydown="switchTab"
        >
          <button
            id="session-tab-conversation"
            type="button"
            role="tab"
            aria-controls="session-panel-conversation"
            :aria-selected="view === 'conversation'"
            :tabindex="view === 'conversation' ? 0 : -1"
            @click="view = 'conversation'"
          >
            对话
          </button>
          <button
            id="session-tab-trace"
            type="button"
            role="tab"
            aria-controls="session-panel-trace"
            :aria-selected="view === 'trace'"
            :tabindex="view === 'trace' ? 0 : -1"
            @click="view = 'trace'"
          >
            调用轨迹
          </button>
        </div>
        <button
          v-if="view === 'trace' && pendingRequests.length"
          type="button"
          class="pending-shortcut"
          @click="showRetrySource(pendingRequests[0]!.runId)"
        >
          <span class="state-dot" />
          {{ pendingRequests.length }} 项等待输入
          <small>去处理 →</small>
        </button>
        <NButton
          v-if="view === 'trace' && activeRun"
          text
          size="small"
          :disabled="runtime.connectionStatus !== 'connected'"
          :loading="!!runtime.pendingCancellations[activeRun.id]"
          @click="stopTraceRun"
        >
          停止运行
        </NButton>
      </div>
    </header>

    <main
      v-show="view === 'conversation'"
      id="session-panel-conversation"
      ref="scrollElement"
      role="tabpanel"
      aria-labelledby="session-tab-conversation"
      tabindex="0"
      class="conversation-scroll app-scrollbar"
    >
      <div class="conversation-column">
        <NButton
          v-if="session.nextBeforeSeq"
          size="small"
          @click="sessions.loadOlder(session.id)"
        >
          加载更早消息
        </NButton>
        <template
          v-for="message in orphanMessages"
          :key="message.id"
        >
          <div
            v-if="message.role === 'user'"
            class="user-block"
          >
            <div class="user-message">{{ message.content }}</div>
          </div>
          <article
            v-else
            class="assistant-message"
          >
            <MarkdownContent :content="message.content" />
          </article>
        </template>

        <section
          v-for="(entry, index) in timeline"
          :id="`run-${entry.run.id}`"
          :key="entry.run.id"
          :data-run-id="entry.run.id"
          class="run-section"
        >
          <div
            v-if="entry.run.retryOfRunId"
            class="retry-source"
          >
            <span>{{ entry.run.retryTaskId ? '任务重试' : '未完成任务重试' }}</span>
            <NButton
              text
              size="tiny"
              @click="showRetrySource(entry.run.retryOfRunId)"
            >
              查看来源
            </NButton>
          </div>
          <div
            v-if="entry.user"
            class="user-block"
          >
            <div class="user-message">{{ entry.user.content }}</div>
            <div
              v-if="entry.user.attachments.length"
              class="message-files"
            >
              <span
                v-for="file in entry.user.attachments"
                :key="file.id"
              >
                <AppIcon :component="File" />
                {{ file.name }}
              </span>
            </div>
          </div>

          <div class="run-bridge">
            <div class="agent-signature">
              <strong>Aurora</strong>
              <span
                v-if="['failed', 'cancelled', 'interrupted'].includes(entry.run.status)"
                class="bridge-state"
                :class="entry.run.status"
              >
                {{
                  {
                    queued: '排队中',
                    running: '执行中',
                    waiting: '等待输入',
                    completed: '已完成',
                    failed: '执行失败',
                    cancelled: '已取消',
                    interrupted: '已中断',
                  }[entry.run.status]
                }}
              </span>
            </div>
            <button
              type="button"
              class="trace-link"
              :aria-label="`查看第 ${index + 1} 轮调用轨迹`"
              @click="openTrace(entry.run.id)"
            >
              查看轨迹
            </button>
          </div>
          <RunReview
            :run-id="entry.run.id"
            :status="entry.run.status"
            reviews-only
          />
          <NButton
            v-if="['failed', 'cancelled', 'interrupted'].includes(entry.run.status)"
            size="small"
            secondary
            :disabled="
              runtime.connectionStatus !== 'connected' ||
              !!activeRun ||
              runtime.retryPendingFor(session.id)
            "
            :loading="runtime.retryPendingFor(session.id)"
            aria-label="重试运行"
            @click="retryRun(entry.run.id)"
          >
            重试未完成任务
          </NButton>

          <ApprovalCard
            v-for="request in entry.requests"
            :key="request.id"
            :request="request"
            :submitting="!!runtime.pendingInputs[request.id]"
            @decide="decideApproval"
            @respond="respondToInput"
          />

          <template
            v-for="message in entry.responses"
            :key="message.id"
          >
            <article
              v-if="message.kind === 'error' || message.status === 'failed'"
              class="error-message"
            >
              <AppIcon
                :component="AlertTriangle"
                :size="18"
              />
              <span>{{ message.content }}</span>
            </article>
            <article
              v-else-if="message.role === 'tool' || message.kind === 'tool'"
              class="tool-message"
            >
              <MarkdownContent :content="message.content" />
            </article>
            <article
              v-else
              class="assistant-message"
            >
              <MarkdownContent :content="message.content" />
              <span
                v-if="message.status === 'streaming'"
                class="cursor"
              />
            </article>
          </template>

          <div
            v-if="['queued', 'running'].includes(entry.run.status)"
            class="working-row"
          >
            <NSpin :size="16" />
            {{ entry.run.status === 'queued' ? '排队中…' : '正在执行…' }}
          </div>
        </section>

        <div
          v-if="session.status === 'running' && !activeRun"
          class="working-row"
        >
          <NSpin :size="16" />
          正在规划…
        </div>
      </div>
    </main>
    <div
      v-if="view === 'trace'"
      id="session-panel-trace"
      role="tabpanel"
      aria-labelledby="session-tab-trace"
      class="trace-pane"
      tabindex="0"
    >
      <TracePanel
        :session="session"
        :initial-run-id="traceRunId"
        @select="traceRunId = $event"
      />
    </div>
    <GitChangesDrawer
      v-model:show="showChanges"
      :session="session"
    />
  </div>
</template>

<style scoped>
.conversation-view {
  display: grid;
  width: 100%;
  height: 100%;
  grid-template-rows: auto minmax(0, 1fr);
  overflow: hidden;
}
.conversation-header {
  border-bottom: 1px solid var(--border);
  background: color-mix(in srgb, var(--surface) 55%, transparent);
}
.header-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  min-height: 42px;
  padding: 6px 24px;
}
.title-row {
  display: flex;
  flex: 1;
  min-width: 0;
  align-items: center;
  gap: 11px;
}
.title-row h1 {
  overflow: hidden;
  margin: 0;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
}
.view-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 38px;
  padding: 0 24px;
  gap: 16px;
}
.view-tabs {
  display: flex;
  align-items: stretch;
  gap: 23px;
  align-self: stretch;
}
.view-tabs > button {
  position: relative;
  display: flex;
  align-items: center;
  gap: 7px;
  border: 0;
  padding: 0 1px;
  background: transparent;
  color: var(--text-muted);
  font: inherit;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
}
.view-tabs > button[aria-selected='true'] {
  color: var(--text);
  font-weight: 600;
}
.view-tabs > button[aria-selected='true']::after {
  content: '';
  position: absolute;
  bottom: -1px;
  left: 0;
  right: 0;
  height: 2px;
  background: var(--accent);
  border-radius: 3px 3px 0 0;
}
.pending-shortcut {
  display: flex;
  align-items: center;
  gap: 7px;
  padding: 6px 9px;
  color: var(--accent);
  background: var(--accent-soft);
  border: 0;
  border-radius: 6px;
  font-size: 11px;
  cursor: pointer;
}
.pending-shortcut small {
  font-size: 10px;
  margin-left: 5px;
}
.state-dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: currentColor;
}
.trace-pane {
  overflow: hidden;
  min-height: 0;
}
.conversation-scroll {
  overflow: auto;
  padding: 28px clamp(24px, 5vw, 80px) 170px;
}
.conversation-column {
  width: min(800px, 100%);
  margin: 0 auto;
}
.run-section {
  position: relative;
  padding: 0 0 28px;
  margin: 0 0 28px;
  border-bottom: 1px solid var(--border);
  scroll-margin-top: 22px;
}
.run-section:last-of-type {
  border-bottom: 0;
  margin-bottom: 0;
}
.user-block {
  margin: 0 0 20px;
  max-width: 100%;
}
.user-message {
  font-size: 16px;
  font-weight: 500;
  line-height: 1.65;
  letter-spacing: -0.025em;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}
.message-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.message-files span {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 5px;
  color: var(--text-muted);
  font-size: 10px;
}
.message-files svg {
  width: 13px;
}
.retry-source {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  color: var(--text-muted);
  font-size: 11px;
}
.run-bridge {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 20px 0 14px;
}
.agent-signature {
  display: flex;
  align-items: center;
  gap: 8px;
}
.agent-signature strong {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.02em;
}
.bridge-state {
  padding-left: 8px;
  margin-left: 2px;
  border-left: 1px solid var(--border);
  color: var(--text-muted);
  font-size: 10px;
}
.bridge-state.running {
  color: var(--accent);
}
.bridge-state.failed {
  color: var(--danger);
}
.trace-link {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 5px 0;
  background: transparent;
  color: var(--text-muted);
  border: 0;
  font-size: 11px;
  cursor: pointer;
}
.trace-link:hover {
  color: var(--accent);
}
.assistant-message {
  position: relative;
  margin: 0 0 25px;
  padding-left: 0;
}
.conversation-view :deep(pre),
.conversation-view :deep(code) {
  font-family: var(--font-ui);
}
.assistant-message :deep(.markdown-content) {
  font-size: 14px;
  line-height: 1.95;
}
.assistant-message :deep(.markdown-content p) {
  margin-bottom: 16px;
}
.assistant-message :deep(.markdown-content h1),
.assistant-message :deep(.markdown-content h2) {
  font-size: 19px;
}
.cursor {
  display: inline-block;
  width: 6px;
  height: 15px;
  margin-left: 2px;
  background: var(--text);
  animation: blink 1s steps(2) infinite;
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
@media (prefers-reduced-motion: reduce) {
  .cursor {
    animation: none;
  }
}
.error-message {
  display: flex;
  gap: 9px;
  margin: 16px 0 22px;
  padding: 12px;
  border: 1px solid color-mix(in srgb, var(--danger) 30%, transparent);
  border-radius: 9px;
  background: color-mix(in srgb, var(--danger) 7%, transparent);
  color: var(--danger);
  font-size: 13px;
  overflow-wrap: anywhere;
}
.tool-message {
  margin: 8px 0 20px;
  padding-left: 15px;
  border-left: 2px solid var(--border);
}
.tool-message :deep(.markdown-content) {
  font-size: 12px;
  color: var(--text-muted);
}
.working-row {
  display: flex;
  align-items: center;
  gap: 9px;
  margin: 18px 0;
  color: var(--text-muted);
  font-size: 12px;
}
button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 4px;
}
@media (max-width: 680px) {
  .header-top {
    padding: 6px 16px;
  }
  .view-bar {
    padding: 0 16px;
  }
  .view-tabs {
    gap: 20px;
  }
  .pending-shortcut {
    font-size: 10px;
  }
  .pending-shortcut small {
    display: none;
  }
  .conversation-scroll {
    padding: 24px 20px 170px;
  }
  .user-message {
    font-size: 16px;
  }
  .assistant-message {
    padding-left: 0;
  }
  .run-bridge {
    margin-bottom: 19px;
  }
  .trace-link {
    gap: 5px;
  }
  .assistant-message :deep(.markdown-content) {
    font-size: 14px;
  }
  .run-section {
    margin-bottom: 26px;
    padding-bottom: 26px;
  }
}
</style>
