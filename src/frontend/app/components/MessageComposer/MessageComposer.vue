<script setup lang="ts">
import { NButton, NInput } from 'naive-ui'
import { ArrowUp } from '~/utils/icons'
import type { AttachmentRef } from '~/types/agent'

const props = withDefaults(
  defineProps<{
    disabled?: boolean
    loading?: boolean
    placeholder?: string
  }>(),
  { placeholder: '描述你希望 Agent 完成的任务' },
)
const value = defineModel<string>({ default: '' })
const emit = defineEmits<{ submit: [objective: string, attachments: AttachmentRef[]] }>()
const resolvedPlaceholder = computed(() =>
  props.loading ? 'Aurora 正在处理任务…' : props.placeholder,
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
    :class="{ disabled }"
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
      <span class="composer-hint">Enter 发送 · Shift+Enter 换行</span>
      <NButton
        v-if="loading"
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
        @click="submit"
      >
        <template #icon><AppIcon :component="ArrowUp" /></template>
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.composer-shell {
  padding: 20px 24px 16px;
  border: 1px solid var(--glass-edge);
  border-radius: 24px;
  background: var(--glass-fill);
  box-shadow: var(--material-shadow);
  -webkit-backdrop-filter: blur(var(--material-blur)) saturate(135%);
  backdrop-filter: blur(var(--material-blur)) saturate(135%);
  transition:
    border-color 180ms ease,
    box-shadow 180ms ease;
}

.composer-shell:focus-within {
  border-color: color-mix(in srgb, var(--accent) 35%, var(--glass-edge));
  box-shadow:
    var(--material-shadow),
    0 0 0 3px color-mix(in srgb, var(--accent) 5%, transparent);
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
  padding-top: 14px;
}

.prompt-shortcut {
  color: var(--text-muted);
  font-size: 11px;
}

.prompt-shortcut:hover {
  color: var(--accent);
}

.composer-hint {
  flex: 1;
  color: var(--text-muted);
  font-size: 10px;
  text-align: right;
}

@media (max-width: 620px) {
  .composer-shell {
    padding: 18px;
    border-radius: 20px;
  }

  .composer-hint {
    visibility: hidden;
    font-size: 0;
  }
}
</style>
