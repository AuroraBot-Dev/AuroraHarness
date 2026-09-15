<script setup lang="ts">
import { NButton, NIcon, NInput, NTag, NTooltip } from 'naive-ui'
import { ArrowUp, Paperclip } from '@vicons/tabler'
import type { AttachmentRef } from '~/types/agent'

const props = withDefaults(defineProps<{ disabled?: boolean; loading?: boolean; placeholder?: string }>(), { placeholder: '描述你希望 Agent 完成的任务' })
const emit = defineEmits<{ submit: [objective: string, attachments: AttachmentRef[]]; stop: [] }>()
const value = ref('')
const attachments = ref<AttachmentRef[]>([])
const resolvedPlaceholder = computed(() => props.loading ? '可以停止当前运行，或切换到其他会话' : props.disabled ? '运行时未连接，仍可浏览历史' : props.placeholder)

function submit() {
  const objective = value.value.trim()
  if (!objective || props.disabled || props.loading) return
  emit('submit', objective, [...attachments.value]); value.value = ''; attachments.value = []
}
function onKeydown(event: KeyboardEvent) { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); submit() } }
</script>

<template>
  <div class="composer-shell" :class="{ disabled }">
    <div v-if="attachments.length" class="attachments"><NTag v-for="item in attachments" :key="item.id" closable size="small" @close="attachments = attachments.filter((other) => other.id !== item.id)">{{ item.name }}</NTag></div>
    <NInput v-model:value="value" type="textarea" :autosize="{ minRows: 1, maxRows: 6 }" :placeholder="resolvedPlaceholder" :disabled="disabled" class="composer-input" @keydown="onKeydown" />
    <div class="composer-footer">
      <NTooltip><template #trigger><span><NButton quaternary circle size="small" disabled aria-label="添加文件引用"><template #icon><NIcon :component="Paperclip" /></template></NButton></span></template>当前后端暂不支持附件</NTooltip>
      <span class="composer-hint">Enter 发送 · Shift+Enter 换行</span>
      <NButton v-if="loading" circle secondary size="small" disabled loading aria-label="运行中" />
      <NButton v-else circle type="primary" size="small" :disabled="disabled || !value.trim()" aria-label="发送" @click="submit"><template #icon><NIcon :component="ArrowUp" /></template></NButton>
    </div>
  </div>
</template>

<style scoped>
.composer-shell{padding:10px 12px 9px;border:1px solid var(--border);border-radius:16px;background:var(--surface);box-shadow:var(--shadow);transition:border-color 120ms ease}.composer-shell:focus-within{border-color:#a1a1aa}.composer-shell.disabled{box-shadow:none;opacity:.8}.attachments{display:flex;flex-wrap:wrap;gap:6px;padding:0 0 7px}.composer-input :deep(.n-input-wrapper){padding:0!important}.composer-input :deep(.n-input__border),.composer-input :deep(.n-input__state-border){display:none}.composer-input :deep(textarea){padding:3px 2px 8px!important;font-size:14px;line-height:1.55}.composer-footer{display:flex;align-items:center;gap:8px}.composer-hint{flex:1;color:var(--text-muted);font-size:11px}@media(max-width:620px){.composer-hint{display:none}}
</style>
