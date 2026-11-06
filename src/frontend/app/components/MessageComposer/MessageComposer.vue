<script setup lang="ts">
import { NButton, NInput } from 'naive-ui'
import { ArrowUp, Stop } from '~/utils/icons'
import type { AttachmentRef } from '~/types/agent'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    compact?: boolean
    loading?: boolean
    canStop?: boolean
    stopping?: boolean
    placeholder?: string
  }>(),
  { placeholder: '描述你希望 Agent 完成的任务' },
)
const value = defineModel<string>({ default: '' })
const emit = defineEmits<{
  submit: [objective: string, attachments: AttachmentRef[]]
  stop: []
}>()
const resolvedPlaceholder = computed(() =>
  props.stopping
    ? '正在停止，等待运行时确认…'
    : props.loading
      ? 'Aurora 正在处理任务…'
      : props.placeholder,
)

function submit() {
  const objective = value.value.trim()
  if (!objective || props.disabled || props.loading) return
  value.value = ''
  emit('submit', objective, [])
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <div
    class="composer-shell"
    :class="{ disabled, compact }"
  >
    <div
      v-if="$slots.context"
      class="composer-context"
    >
      <slot name="context" />
    </div>
    <NInput
      v-model:value="value"
      :input-props="{ 'aria-label': '任务内容' }"
      type="textarea"
      :autosize="{ minRows: 1, maxRows: 8 }"
      :placeholder="resolvedPlaceholder"
      :disabled="disabled || loading"
      class="composer-input"
      @keydown="onKeydown"
    />
    <div class="composer-footer">
      <NuxtLink
        to="/prompts"
        class="prompt-shortcut"
      >
        提示词库
      </NuxtLink>
      <NButton
        v-if="loading && canStop"
        circle
        secondary
        size="small"
        :disabled="disabled || stopping"
        :loading="stopping"
        :aria-label="stopping ? '正在停止' : '停止运行'"
        @click="emit('stop')"
      >
        <template #icon><AppIcon :component="Stop" /></template>
      </NButton>
      <NButton
        v-else-if="loading"
        circle
        secondary
        size="small"
        disabled
        loading
        aria-label="运行中"
      />
      <NButton
        v-else
        circle
        type="primary"
        size="small"
        :disabled="disabled || !value.trim()"
        aria-label="发送"
        title="发送（Enter）"
        @click="submit"
      >
        <template #icon><AppIcon :component="ArrowUp" /></template>
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.composer-shell {
  padding: 14px 16px 10px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: var(--surface);
  transition: border-color 180ms ease;
}

.composer-shell:focus-within {
  border-color: var(--accent);
}

.composer-shell:not(.compact) {
  background: var(--glass-fill);
  border-radius: 24px;
  box-shadow: var(--material-shadow);
  -webkit-backdrop-filter: blur(var(--material-blur)) saturate(135%);
  backdrop-filter: blur(var(--material-blur)) saturate(135%);
}

.composer-context {
  margin-bottom: 20px;
}

.composer-input {
  background: transparent !important;
  --n-color-disabled: transparent !important;
}

.composer-input :deep(.n-input-wrapper) {
  padding: 0 !important;
}

.composer-input :deep(.n-input__border),
.composer-input :deep(.n-input__state-border) {
  display: none;
}

.composer-input :deep(textarea) {
  padding: 3px 2px 12px !important;
  font-size: 14px;
  line-height: 1.65;
}

.composer-footer {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-top: 6px;
}

.composer-footer :deep(button:last-child) {
  margin-left: auto;
}

.prompt-shortcut {
  color: var(--text-muted);
  font-size: 11px;
}

.prompt-shortcut:hover {
  color: var(--accent);
}

@media (max-width: 620px) {
  .composer-shell {
    padding: 12px 14px 10px;
  }
}
</style>
