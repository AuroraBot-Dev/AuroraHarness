<script setup lang="ts">
import { NButton, NDrawer, NDrawerContent, NFormItem, NInput } from 'naive-ui'
import type { PromptTemplate } from '~/types/prompt'
import { promptVariables, renderPrompt } from '~/utils/prompts'

const show = defineModel<boolean>('show', { default: false })
const props = defineProps<{ template: PromptTemplate | null }>()
const store = usePromptStore()
const values = ref<Record<string, string>>(Object.create(null))
const variables = computed(() => promptVariables(props.template?.content ?? ''))
const rendered = computed(() => renderPrompt(props.template?.content ?? '', values.value))
const complete = computed(() => variables.value.every((name) => values.value[name]?.trim()))

watch(show, (visible) => {
  if (visible) {
    values.value = Object.create(null)
  }
})

async function useTemplate() {
  if (!complete.value || !props.template) return
  store.draft = rendered.value
  show.value = false
  await navigateTo('/')
}
</script>

<template>
  <NDrawer
    v-model:show="show"
    :width="540"
    :auto-focus="false"
    style="max-width: 100vw"
  >
    <NDrawerContent
      :title="template?.name || '使用提示词'"
      closable
    >
      <p class="use-intro">
        {{
          variables.length
            ? '补充这一次的细节，预览完整提示词。'
            : '预览后，将提示词放入工作台继续编辑。'
        }}
      </p>
      <NFormItem
        v-for="variable in variables"
        :key="variable"
        :label="variable"
        required
      >
        <NInput
          v-model:value="values[variable]"
          :input-props="{ 'aria-label': variable }"
          :placeholder="`填写${variable}`"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
      </NFormItem>
      <h3>提示词预览</h3>
      <pre class="prompt-preview">{{ rendered }}</pre>
      <p class="use-intro">只填入输入框；检查并发送后，Agent 才会执行。</p>
      <template #footer>
        <NButton
          type="primary"
          :disabled="!complete"
          @click="useTemplate"
        >
          填入工作台
        </NButton>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.use-intro {
  margin: 0 0 24px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.8;
}

h3 {
  margin-top: 26px;
  font-size: 12px;
  font-weight: 600;
}

.prompt-preview {
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface-muted);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  line-height: 1.9;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
