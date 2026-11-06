<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCheckbox,
  NDrawer,
  NDrawerContent,
  NFormItem,
  NInput,
  NSelect,
  NSpace,
  useMessage,
} from 'naive-ui'
import type { AgentConfig, ModelType } from '~/types/workflow'

const show = defineModel<boolean>('show', { default: false })
const props = defineProps<{ agent: AgentConfig | null; duplicate?: boolean }>()
const store = useAgentStore()
const prompts = usePromptStore()
const message = useMessage()
const saving = ref(false)
const toolsText = ref('')
const selectedPrompt = ref<string | null>(null)
const form = reactive({
  name: '',
  description: '',
  instructions: '',
  model_config_id: '',
  enabled: true,
  callable: false,
})
const labels: Record<ModelType, string> = {
  text: '文本',
  multimodal: '多模态',
  embedding: '向量 · 暂不支持调用',
}
const modelOptions = computed(() =>
  store.models.map((model) => ({
    label: `${model.name} · ${model.modelName === 'unconfigured' ? '未配置模型 ID' : model.modelName} · ${labels[model.modelType]}`,
    value: model.id,
    disabled: !model.enabled || model.modelType === 'embedding',
  })),
)
const promptOptions = computed(() =>
  prompts.items.map((item) => ({ label: item.name, value: item.id })),
)

watch(show, (visible) => {
  if (!visible) return
  const agent = props.agent
  Object.assign(form, {
    name: agent ? `${agent.name}${props.duplicate ? ' 副本' : ''}` : '',
    description: agent?.description ?? '',
    instructions: agent?.instructions ?? '',
    model_config_id: agent?.modelConfigId ?? '',
    enabled: agent?.enabled ?? true,
    callable: props.duplicate ? false : (agent?.callable ?? false),
  })
  toolsText.value = agent?.allowedTools.join(', ') ?? ''
  selectedPrompt.value = null
  prompts.hydrate()
})

function insertPrompt() {
  const template = prompts.items.find((item) => item.id === selectedPrompt.value)
  if (!template) return
  form.instructions = [form.instructions, template.content].filter(Boolean).join('\n\n')
  selectedPrompt.value = null
  message.success('已追加到执行 Prompt，请按角色需要调整其中的变量')
}

async function save() {
  saving.value = true
  try {
    await store.save(props.duplicate ? '' : (props.agent?.id ?? ''), {
      ...form,
      name: form.name.trim(),
      allowed_tools: [
        ...new Set(
          toolsText.value
            .split(',')
            .map((tool) => tool.trim())
            .filter(Boolean),
        ),
      ],
    })
    show.value = false
    message.success('Agent 已保存，将在下一轮运行生效')
  } catch (error) {
    message.error(String(error))
  } finally {
    saving.value = false
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
      :title="duplicate ? '复制 Agent' : agent ? '编辑 Agent' : '新建 Agent'"
      closable
    >
      <p class="editor-intro">从角色到工具，把工作的方式交给你定义。</p>
      <NFormItem
        label="名称"
        required
      >
        <NInput
          v-model:value="form.name"
          :input-props="{ 'aria-label': 'Agent 名称' }"
          placeholder="例如：代码审查伙伴"
        />
      </NFormItem>
      <NFormItem label="能力描述 · 告诉主 Agent 何时调用">
        <NInput
          v-model:value="form.description"
          type="textarea"
          placeholder="例如：理解页面截图，定位布局与视觉问题"
          :autosize="{ minRows: 2, maxRows: 5 }"
        />
      </NFormItem>
      <NFormItem
        label="模型"
        required
      >
        <NSelect
          v-model:value="form.model_config_id"
          :options="modelOptions"
          placeholder="选择执行模型"
        />
      </NFormItem>
      <NAlert
        v-if="!store.models.length"
        type="info"
      >
        <NuxtLink to="/settings">先配置一个模型 →</NuxtLink>
      </NAlert>
      <div class="prompt-picker">
        <NSelect
          v-model:value="selectedPrompt"
          :options="promptOptions"
          placeholder="从提示词库选一段作为起点"
          clearable
          aria-label="选择提示词"
        />
        <NButton
          :disabled="!selectedPrompt"
          @click="insertPrompt"
        >
          追加
        </NButton>
      </div>
      <NFormItem label="执行 Prompt">
        <NInput
          v-model:value="form.instructions"
          type="textarea"
          placeholder="描述职责、执行步骤和输出要求"
          :autosize="{ minRows: 7, maxRows: 18 }"
        />
      </NFormItem>
      <NFormItem label="允许工具 · 逗号分隔，* 为全部，留空仅模型推理">
        <NInput
          v-model:value="toolsText"
          placeholder="read_file, list_files"
        />
      </NFormItem>
      <NSpace vertical>
        <NCheckbox v-model:checked="form.enabled">启用 Agent</NCheckbox>
        <NCheckbox v-model:checked="form.callable">允许主 Agent 调用</NCheckbox>
      </NSpace>
      <p class="editor-intro editor-footnote">
        修改在下一轮运行生效。子 Agent
        继承当前任务的沙箱与审批规则。提示词库变量用于任务模板，追加到角色后请替换为固定要求。
      </p>
      <template #footer>
        <NButton
          type="primary"
          :loading="saving"
          :disabled="!form.name.trim() || !form.model_config_id"
          @click="save"
        >
          保存 Agent
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

.prompt-picker {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 8px 0 16px;
}

.prompt-picker > .n-select {
  min-width: 0;
  flex: 1;
}

.editor-footnote {
  margin-top: 22px;
  font-size: 12px;
}
</style>
