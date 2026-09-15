<script setup lang="ts">
import { NButton, NIcon, NInput, NInputNumber, NTag } from "naive-ui";
import { AlertCircle, Check, X } from "@vicons/tabler";
import type { ApprovalRequest } from "~/types/agent";

const props = defineProps<{ request: ApprovalRequest }>();
const emit = defineEmits<{
  decide: [requestId: string, approved: boolean];
  respond: [requestId: string, response: unknown];
}>();
const answer = ref("");
const score = ref(5);
const preview = reactive({ url: 'http://127.0.0.1:3000', command: '', cwd: '.', page: '/' });
function submitPreview() {
  emit('respond', props.request.id, { url: preview.url, command: preview.command, cwd: preview.cwd, pages: [preview.page] });
}

function submitInput() {
  if (props.request.kind === "evaluation")
    emit("respond", props.request.id, {
      score: score.value,
      comment: answer.value.trim(),
    });
  else if (answer.value.trim())
    emit("respond", props.request.id, answer.value.trim());
}
</script>

<template>
  <article class="approval-card">
    <div class="approval-icon">
      <NIcon :component="AlertCircle" :size="19" />
    </div>
    <div class="approval-copy">
      <div class="approval-title">
        {{
          request.kind === "approval" || !request.kind
            ? "需要你的确认"
            : request.kind === "evaluation"
              ? "评价本次结果"
              : "需要补充信息"
        }}
        <NTag size="small" :bordered="false">{{ request.risk }}</NTag>
      </div>
      <p>{{ request.question || request.action }}</p>
      <p v-if="request.details">{{ request.details }}</p>
      <div v-if="request.kind === 'decision'" class="input-row">
        <NButton v-for="action in request.actions || ['accept', 'cancel', 'retry']" :key="action" @click="emit('respond', request.id, { action })">{{ ({ accept: '接受当前结果', cancel: '取消', retry: '再试一次' } as Record<string, string>)[action] }}</NButton>
      </div>
      <div v-else-if="request.previewRequired" class="preview-form">
        <label>预览地址<NInput v-model:value="preview.url" /></label>
        <label>启动命令（留空使用已启动服务）<NInput v-model:value="preview.command" placeholder="npm run dev" /></label>
        <label>工作区内目录<NInput v-model:value="preview.cwd" /></label>
        <label>页面路径<NInput v-model:value="preview.page" /></label>
        <NButton type="primary" @click="submitPreview">保存并继续</NButton>
      </div>
      <div v-else-if="request.kind === 'clarification'" class="input-row">
        <NInput
          v-model:value="answer"
          placeholder="输入补充信息"
          @keydown.enter.prevent="submitInput"
        />
        <NButton type="primary" :disabled="!answer.trim()" @click="submitInput"
          >继续</NButton
        >
      </div>
      <div v-else-if="request.kind === 'evaluation'" class="input-row">
        <NInputNumber v-model:value="score" :min="1" :max="5" />
        <NInput v-model:value="answer" placeholder="可选评价" />
        <NButton type="primary" @click="submitInput">提交</NButton>
      </div>
    </div>
    <div
      v-if="request.kind === 'approval' || !request.kind"
      class="approval-actions"
    >
      <NButton size="small" @click="emit('decide', request.id, false)">
        <template #icon>
          <NIcon :component="X" />
        </template>
        拒绝
      </NButton>
      <NButton
        size="small"
        type="primary"
        @click="emit('decide', request.id, true)"
        ><template #icon><NIcon :component="Check" /></template>允许</NButton
      >
    </div>
  </article>
</template>

<style scoped>
.preview-form{display:grid;gap:10px;margin-top:10px}.preview-form label{display:grid;gap:4px;font-size:12px}
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
