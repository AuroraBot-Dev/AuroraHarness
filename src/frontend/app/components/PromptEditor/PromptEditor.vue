<script setup lang="ts">
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NFormItem,
  NInput,
  NTag,
  useMessage,
} from 'naive-ui'
import type { PromptInput, PromptTemplate } from '~/types/prompt'
import { maxPromptLength, promptVariables } from '~/utils/prompts'

const show = defineModel<boolean>('show', { default: false })
const props = defineProps<{ template: PromptTemplate | null }>()
const emit = defineEmits<{ saved: [] }>()
const store = usePromptStore()
const message = useMessage()
const form = reactive<PromptInput>({ name: '', description: '', content: '' })
const variables = computed(() => promptVariables(form.content))
const variableExample = '{{目标}}'

watch(show, (visible) => {
  if (visible)
    Object.assign(form, {
      name: props.template?.name ?? '',
      description: props.template?.description ?? '',
      content: props.template?.content ?? '',
    })
})

function save() {
  try {
    store.save(props.template?.id ?? null, form)
    show.value = false
    emit('saved')
    message.success('提示词已保存')
  } catch (error) {
    message.error(String(error instanceof Error ? error.message : error))
  }
}
</script>

<template>
  <NDrawer
    v-model:show="show"
    :width="580"
    :auto-focus="false"
    style="max-width: 100vw"
  >
    <NDrawerContent
      :title="template ? '编辑提示词' : '新建提示词'"
      closable
    >
      <p class="editor-intro">写下值得复用的工作方式，细节留到每次使用时填写。</p>
      <NFormItem
        label="名称"
        required
      >
        <NInput
          v-model:value="form.name"
          :input-props="{ 'aria-label': '提示词名称' }"
          placeholder="给这段提示词起个名字"
          :maxlength="100"
        />
      </NFormItem>
      <NFormItem label="简介">
        <NInput
          v-model:value="form.description"
          :input-props="{ 'aria-label': '提示词简介' }"
          placeholder="什么时候使用它？"
          :maxlength="500"
        />
      </NFormItem>
      <NFormItem
        label="提示词正文"
        required
      >
        <NInput
          v-model:value="form.content"
          :input-props="{ 'aria-label': '提示词正文' }"
          type="textarea"
          placeholder="描述目标、约束与输出格式。使用 {{变量名}} 留出每次变化的部分。"
          :maxlength="maxPromptLength"
          :autosize="{ minRows: 12, maxRows: 24 }"
          show-count
        />
      </NFormItem>
      <NAlert
        type="info"
        :show-icon="false"
      >
        用双花括号定义变量，例如 {{ variableExample }}。使用时会提示填写，不会自动发送任务。
      </NAlert>
      <div
        v-if="variables.length"
        class="variable-list"
      >
        <span>已识别变量</span>
        <NTag
          v-for="variable in variables"
          :key="variable"
          size="small"
          :bordered="false"
        >
          {{ variable }}
        </NTag>
      </div>
      <template #footer>
        <NButton
          type="primary"
          :disabled="!form.name.trim() || !form.content.trim()"
          @click="save"
        >
          保存提示词
        </NButton>
      </template>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.editor-intro {
  margin: 0 0 24px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.8;
}

.variable-list {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 18px;
}

.variable-list > span {
  color: var(--text-muted);
  font-size: 12px;
}
</style>
