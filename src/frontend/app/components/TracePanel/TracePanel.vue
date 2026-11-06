<script setup lang="ts">
import { NButton, NSpin } from 'naive-ui'
import type { SessionRecord } from '~/types/agent'
import { Activity, AlertTriangle, Check, ChevronDown, ChevronRight, Clock } from '~/utils/icons'
import {
  formatTraceDuration,
  traceInput,
  tracePreview,
  traceStatus,
  traceTime,
} from '~/utils/trace'

const props = defineProps<{ session: SessionRecord; initialRunId?: string }>()
const emit = defineEmits<{ select: [runId: string] }>()
const traces = useTraceStore()
const runtime = useRuntimeStore()
const selectedId = ref(props.initialRunId || props.session.runs.at(-1)?.id || '')
const selectedRun = computed(() => props.session.runs.find((run) => run.id === selectedId.value))
const trace = computed(() => (selectedId.value ? traces.ensure(selectedId.value) : null))
const expanded = ref('')
const issueStates = ['failed', 'cancelled', 'interrupted']
function roleName(agentRunId: string) {
  return trace.value?.stages.find((item) => item.id === agentRunId)?.agentName || ''
}
watch(
  () => props.initialRunId,
  (id) => {
    if (id) selectedId.value = id
  },
)
watch(
  () => props.session.runs.at(-1)?.id,
  (id, previous) => {
    if (id && (!selectedId.value || selectedId.value === previous)) selectedId.value = id
  },
)
watch(
  selectedId,
  (id) => {
    expanded.value = ''
    if (id) {
      emit('select', id)
      void traces.load(id)
    }
  },
  { immediate: true },
)
watch(
  () => runtime.connectionStatus,
  (state) => {
    if (state === 'connected' && selectedId.value) void traces.load(selectedId.value)
  },
)
let reloadTimer: ReturnType<typeof setTimeout> | undefined
watch(
  () => trace.value?.revision,
  () => {
    clearTimeout(reloadTimer)
    reloadTimer = setTimeout(() => {
      if (selectedId.value) void traces.load(selectedId.value)
    }, 180)
  },
)
onBeforeUnmount(() => clearTimeout(reloadTimer))
</script>

<template>
  <section
    class="trace-panel app-scrollbar"
    aria-label="调用时间线"
  >
    <div class="timeline-column">
      <p
        v-if="runtime.connectionStatus !== 'connected'"
        class="trace-notice"
        role="status"
      >
        运行时已断开，显示已加载的记录。
      </p>
      <div
        v-if="trace?.error"
        class="trace-error"
        role="alert"
      >
        <AppIcon :component="AlertTriangle" />
        <span>轨迹读取失败：{{ trace.error }}</span>
        <NButton
          text
          size="small"
          @click="traces.load(selectedId)"
        >
          重试
        </NButton>
      </div>
      <div
        v-if="trace?.loading && !trace.loaded && !trace.calls.length"
        class="trace-empty"
      >
        <NSpin :size="20" />
        <span>正在读取调用记录</span>
      </div>
      <p
        v-else-if="!trace?.calls.length"
        class="trace-empty"
      >
        {{ selectedRun?.status === 'waiting' ? '等待确认后继续' : '暂无工具调用记录' }}
      </p>
      <ol
        v-else
        class="call-timeline"
      >
        <li
          v-for="call in trace.calls"
          :key="call.id"
          class="call-item"
          :class="call.status"
          :data-call-id="call.id"
        >
          <span class="call-marker">
            <AppIcon
              :component="
                call.status === 'completed'
                  ? Check
                  : call.status === 'running'
                    ? Activity
                    : issueStates.includes(call.status)
                      ? AlertTriangle
                      : Clock
              "
              :size="14"
            />
          </span>
          <button
            type="button"
            class="call-summary"
            :aria-expanded="expanded === call.id"
            :aria-controls="'call-detail-' + call.id"
            @click="expanded = expanded === call.id ? '' : call.id"
          >
            <span class="call-description">
              <span class="tool-name">
                {{ call.toolName }}
                <span
                  class="call-status"
                  :class="call.status"
                >
                  {{ traceStatus[call.status] || call.status }}
                </span>
              </span>
              <span
                v-if="tracePreview(call.input)"
                class="call-preview"
              >
                {{ tracePreview(call.input) }}
              </span>
            </span>
            <span class="call-timing">
              <strong>{{ formatTraceDuration(call.durationMs) }}</strong>
              <span>{{ traceTime(call.startedAt) }}</span>
            </span>
            <AppIcon
              :component="expanded === call.id ? ChevronDown : ChevronRight"
              :size="14"
            />
          </button>
          <div
            v-if="expanded === call.id"
            :id="'call-detail-' + call.id"
            class="call-detail"
          >
            <div class="call-attribution">
              <span v-if="roleName(call.agentRunId)">{{ roleName(call.agentRunId) }}</span>
              <span>任务 {{ call.taskKey }}</span>
              <span v-if="call.status === 'running'">执行中 · 完成后显示耗时</span>
            </div>
            <div class="payload-block">
              <h4>输入参数</h4>
              <pre>{{ traceInput(call.input) }}</pre>
            </div>
            <div
              v-if="call.output"
              class="payload-block"
            >
              <h4>调用结果</h4>
              <pre>{{ call.output }}</pre>
            </div>
            <div
              v-if="call.error"
              class="payload-block error-payload"
            >
              <h4>{{ call.status === 'cancelled' ? '取消原因' : '错误信息' }}</h4>
              <pre>{{ call.error }}</pre>
            </div>
            <p
              v-if="call.status === 'interrupted' && call.durationMs === null"
              class="trace-note"
            >
              运行时退出前未记录结束时间，耗时未知。
            </p>
          </div>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.trace-panel {
  min-height: 0;
  height: 100%;
  overflow: auto;
  padding: 28px clamp(20px, 5vw, 72px) 48px;
}
.timeline-column {
  max-width: 850px;
  margin: 0 auto;
}
.call-timeline {
  padding: 0 0 0 16px;
  margin: 0;
  list-style: none;
}
.call-item {
  position: relative;
  border-left: 1px solid var(--border);
  padding-left: 23px;
}
.call-item:last-child {
  border-color: transparent;
}
.call-marker {
  position: absolute;
  left: -11px;
  top: 20px;
  display: grid;
  width: 21px;
  height: 24px;
  border-radius: 50%;
  place-items: center;
  background: var(--app-bg);
  color: var(--text-muted);
}
.call-item.completed .call-marker {
  color: var(--success);
}
.call-item.running .call-marker {
  color: var(--accent);
}
.call-item.failed .call-marker {
  color: var(--danger);
}
.call-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
  min-width: 0;
  padding: 19px 4px;
  text-align: left;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--text);
  cursor: pointer;
}
.call-summary:hover .tool-name,
.call-summary[aria-expanded='true'] .tool-name {
  color: var(--accent);
}
.call-description {
  display: grid;
  gap: 7px;
  min-width: 0;
  flex: 1;
}
.tool-name {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 12px;
  font-family: var(--font-ui);
  overflow-wrap: anywhere;
}
.call-status {
  font-size: 10px;
  color: var(--text-muted);
  flex-shrink: 0;
}
.call-status.failed {
  color: var(--danger);
}
.call-status.running {
  color: var(--accent);
}
.call-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 11px;
  line-height: 1.6;
  color: var(--text-muted);
}
.call-timing {
  display: grid;
  gap: 7px;
  text-align: right;
  font-size: 10px;
  font-family: var(--font-ui);
  color: var(--text-muted);
  flex-shrink: 0;
}
.call-timing strong {
  color: var(--text);
  font-weight: 400;
}
.call-detail {
  padding: 18px 4px 25px;
  border-bottom: 1px solid var(--border);
}
.call-attribution {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  font-size: 10px;
  color: var(--text-muted);
  margin-bottom: 20px;
  overflow-wrap: anywhere;
}
.payload-block + .payload-block {
  margin-top: 18px;
}
.payload-block h4 {
  margin: 0 0 8px;
  font-size: 10px;
  font-weight: 500;
  color: var(--text-muted);
}
.payload-block pre {
  margin: 0;
  max-height: 360px;
  overflow: auto;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 11px;
  font-family: var(--font-ui);
  line-height: 1.8;
}
.error-payload pre {
  color: var(--danger);
}
.trace-notice,
.trace-note {
  margin: 0 0 18px;
  font-size: 11px;
  line-height: 1.7;
  color: var(--text-muted);
}
.trace-note {
  margin: 18px 0 0;
}
.trace-error {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
  color: var(--danger);
  font-size: 12px;
}
.trace-error > span {
  flex: 1;
  overflow-wrap: anywhere;
}
.trace-empty {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 10px;
  padding: 48px 12px;
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
}
button:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 4px;
}
@media (max-width: 680px) {
  .trace-panel {
    padding: 14px 20px 32px;
  }
  .call-item {
    padding-left: 18px;
  }
  .call-summary {
    gap: 10px;
    padding: 17px 0;
  }
  .tool-name {
    gap: 8px;
  }
  .call-timeline {
    padding-left: 10px;
  }
}
</style>
