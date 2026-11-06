<script setup lang="ts">
import { NButton, NInput, NInputNumber, NTag } from 'naive-ui'
import { AlertCircle, Check, X } from '~/utils/icons'
import type { ApprovalRequest } from '~/types/agent'

const props = defineProps<{ request: ApprovalRequest; submitting?: boolean }>()
const emit = defineEmits<{
  decide: [requestId: string, approved: boolean]
  respond: [requestId: string, response: unknown]
}>()
const answer = ref('')
const score = ref(5)
const currentTime = ref(Date.now())
let ticker: ReturnType<typeof setInterval> | undefined
const remaining = computed(() =>
  props.request.expiresAt
    ? Math.max(0, Math.ceil((Date.parse(props.request.expiresAt) - currentTime.value) / 1000))
    : null,
)
const pastDeadline = computed(() => remaining.value !== null && remaining.value <= 0)
const pending = computed(() => props.request.status === 'pending')
const resultText = computed(() =>
  props.request.resolution === 'timeout'
    ? '审批已超时，操作已拒绝'
    : props.request.status === 'approved'
      ? '已允许此操作'
      : props.request.status === 'rejected'
        ? '已拒绝此操作'
        : '审批已失效，操作未获批准',
)
watch(
  () => [props.request.status, props.request.expiresAt],
  () => {
    if (ticker) clearInterval(ticker)
    ticker = undefined
    currentTime.value = Date.now()
    if (pending.value && props.request.expiresAt)
      ticker = setInterval(() => {
        currentTime.value = Date.now()
      }, 250)
  },
  { immediate: true },
)
onBeforeUnmount(() => {
  if (ticker) clearInterval(ticker)
})
const actionLabels: Record<string, string> = {
  accept: '接受当前结果',
  cancel: '取消',
  retry: '再试一次',
}
const preview = reactive({ url: 'http://127.0.0.1:3000', command: '', cwd: '.', page: '/' })
function submitPreview() {
  emit('respond', props.request.id, {
    url: preview.url,
    command: preview.command,
    cwd: preview.cwd,
    pages: [preview.page],
  })
}

function submitInput() {
  if (props.request.kind === 'evaluation')
    emit('respond', props.request.id, {
      score: score.value,
      comment: answer.value.trim(),
    })
  else if (answer.value.trim()) emit('respond', props.request.id, answer.value.trim())
}
</script>

<template>
  <article class="approval-card">
    <div class="approval-icon">
      <AppIcon
        :component="AlertCircle"
        :size="19"
      />
    </div>
    <div class="approval-copy">
      <div class="approval-title">
        {{
          request.kind === 'approval' || !request.kind
            ? pending
              ? '需要你的确认'
              : '审批结果'
            : request.kind === 'evaluation'
              ? '评价本次结果'
              : '需要补充信息'
        }}
        <NTag
          size="small"
          :bordered="false"
        >
          {{ request.risk }}
        </NTag>
      </div>
      <p>{{ request.question || request.action }}</p>
      <p v-if="request.details">{{ request.details }}</p>
      <p
        v-if="!pending"
        role="status"
      >
        {{ resultText }}
      </p>
      <p
        v-else-if="pastDeadline"
        role="status"
      >
        期限已到，等待运行时确认
      </p>
      <p
        v-else-if="submitting"
        role="status"
      >
        正在提交，等待运行时确认
      </p>
      <p
        v-else-if="remaining !== null"
        role="timer"
      >
        {{ remaining }} 秒后自动拒绝
      </p>
      <div
        v-if="pending && request.kind === 'decision'"
        class="input-row"
      >
        <NButton
          v-for="action in request.actions || ['accept', 'cancel', 'retry']"
          :key="action"
          @click="emit('respond', request.id, { action })"
        >
          {{ actionLabels[action] ?? action }}
        </NButton>
      </div>
      <div
        v-else-if="pending && request.previewRequired"
        class="preview-form"
      >
        <label>
          预览地址
          <NInput v-model:value="preview.url" />
        </label>
        <label>
          启动命令（留空使用已启动服务）
          <NInput
            v-model:value="preview.command"
            placeholder="npm run dev"
          />
        </label>
        <label>
          工作区内目录
          <NInput v-model:value="preview.cwd" />
        </label>
        <label>
          页面路径
          <NInput v-model:value="preview.page" />
        </label>
        <NButton
          type="primary"
          @click="submitPreview"
        >
          保存并继续
        </NButton>
      </div>
      <div
        v-else-if="pending && request.kind === 'clarification'"
        class="input-row"
      >
        <NInput
          v-model:value="answer"
          placeholder="输入补充信息"
          @keydown.enter.prevent="submitInput"
        />
        <NButton
          type="primary"
          :disabled="!answer.trim()"
          @click="submitInput"
        >
          继续
        </NButton>
      </div>
      <div
        v-else-if="pending && request.kind === 'evaluation'"
        class="input-row"
      >
        <NInputNumber
          v-model:value="score"
          :min="1"
          :max="5"
        />
        <NInput
          v-model:value="answer"
          placeholder="可选评价"
        />
        <NButton
          type="primary"
          @click="submitInput"
        >
          提交
        </NButton>
      </div>
    </div>
    <div
      v-if="pending && (request.kind === 'approval' || !request.kind)"
      class="approval-actions"
    >
      <NButton
        size="small"
        :disabled="submitting || pastDeadline"
        @click="emit('decide', request.id, false)"
      >
        <template #icon>
          <AppIcon :component="X" />
        </template>
        拒绝
      </NButton>
      <NButton
        size="small"
        type="primary"
        :disabled="submitting || pastDeadline"
        @click="emit('decide', request.id, true)"
      >
        <template #icon><AppIcon :component="Check" /></template>
        允许
      </NButton>
    </div>
  </article>
</template>

<style scoped>
.preview-form {
  display: grid;
  gap: 10px;
  margin-top: 10px;
}
.preview-form label {
  display: grid;
  gap: 4px;
  font-size: 12px;
}
.approval-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  margin: 14px 0;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}

.approval-icon {
  display: grid;
  width: 32px;
  height: 32px;
  flex: 0 0 32px;
  place-items: center;
  border-radius: 8px;
  background: rgb(214 158 46 / 12%);
  color: #b7791f;
}

.approval-copy {
  min-width: 0;
  flex: 1;
}

.approval-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 650;
}

.approval-copy p {
  margin: 5px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.55;
}

.approval-actions,
.input-row {
  display: flex;
  gap: 8px;
}

.input-row {
  margin-top: 10px;
}

@media (max-width: 680px) {
  .approval-card {
    flex-wrap: wrap;
  }
  .approval-actions {
    width: 100%;
    justify-content: flex-end;
  }
  .input-row {
    flex-wrap: wrap;
  }
}
</style>
