<script setup lang="ts">
import { NButton, NCard, NCheckbox, NCollapse, NCollapseItem, NFormItem, NInput, NInputNumber, NSelect, useMessage } from 'naive-ui'
import { runtimeRequest } from '~/utils/runtimeClient'
import type { ProviderConfig, ModelConfig, AgentConfig, WorkflowConfig } from '~/types/workflow'

const message = useMessage()
const providers = ref<ProviderConfig[]>([])
const models = ref<ModelConfig[]>([])
const agents = ref<AgentConfig[]>([])
const workflows = ref<WorkflowConfig[]>([])
const busy = ref(false)
const selected = reactive({ provider: '', model: '', agent: '', workflow: '' })
const provider = reactive({ name: '', base_url: 'https://api.openai.com/v1', credential_ref: 'env:AGENT_API_KEY', enabled: true })
const secret = ref('')
const model = reactive({ name: '', provider_id: '', model_name: '', context_budget_tokens: 8000, enabled: true })
const imageInput = ref(false)
const temperature = ref(0)
const agent = reactive({ name: '', instructions: '', model_config_id: '', enabled: true })
const allowedTools = ref('*')
const workflow = reactive({ name: '', kind: 'single', code_agent_id: '', review_agent_id: '', max_revisions: 2, enabled: true })
const defaultWorkflow = ref<string | null>(null)
const options = (items: { id: string; name: string }[]) => items.map((item) => ({ label: item.name, value: item.id }))

async function load() {
  const [p, m, a, w, settings] = await Promise.all([
    runtimeRequest<{ items: ProviderConfig[] }>('provider.list'), runtimeRequest<{ items: ModelConfig[] }>('model.list'),
    runtimeRequest<{ items: AgentConfig[] }>('agent.list'), runtimeRequest<{ items: WorkflowConfig[] }>('workflow.list'),
    runtimeRequest<{ defaultWorkflowId: string }>('settings.get'),
  ])
  providers.value = p.items; models.value = m.items; agents.value = a.items; workflows.value = w.items
  defaultWorkflow.value = settings.defaultWorkflowId
}
onMounted(() => load().catch((error) => message.error(String(error))))

function editProvider(id: string | null) {
  selected.provider = id || ''
  const item = providers.value.find((entry) => entry.id === id)
  Object.assign(provider, { name: item?.name || '', base_url: item?.baseUrl || 'https://api.openai.com/v1', credential_ref: item?.credentialRef || 'env:AGENT_API_KEY', enabled: item ? !!item.enabled : true })
  secret.value = ''
}
function editModel(id: string | null) {
  selected.model = id || ''
  const item = models.value.find((entry) => entry.id === id)
  Object.assign(model, { name: item?.name || '', provider_id: item?.providerId || '', model_name: item?.modelName || '', context_budget_tokens: item?.contextBudgetTokens || 8000, enabled: item ? !!item.enabled : true })
  imageInput.value = !!item?.inputTypes.includes('image'); temperature.value = Number(item?.parameters.temperature ?? 0)
}
function editAgent(id: string | null) {
  selected.agent = id || ''
  const item = agents.value.find((entry) => entry.id === id)
  Object.assign(agent, { name: item?.name || '', instructions: item?.instructions || '', model_config_id: item?.modelConfigId || '', enabled: item ? !!item.enabled : true })
  allowedTools.value = item?.allowedTools.join(', ') ?? '*'
}
function editWorkflow(id: string | null) {
  selected.workflow = id || ''
  const item = workflows.value.find((entry) => entry.id === id)
  Object.assign(workflow, { name: item?.name || '', kind: item?.kind || 'single', code_agent_id: item?.steps.find((step) => step.step_key === 'code')?.agent_id || '', review_agent_id: item?.steps.find((step) => step.step_key === 'review')?.agent_id || '', max_revisions: item?.maxRevisions ?? 2, enabled: item ? !!item.enabled : true })
}
async function save(kind: keyof typeof selected) {
  busy.value = true
  try {
    const data = kind === 'provider' ? { ...provider } : kind === 'model' ? { ...model, parameters: { temperature: temperature.value }, input_types: imageInput.value ? ['text', 'image'] : ['text'] } : kind === 'agent' ? { ...agent, allowed_tools: allowedTools.value.split(',').map((item) => item.trim()).filter(Boolean) } : { ...workflow }
    const result = await runtimeRequest<{ id: string }>(`${kind}.${selected[kind] ? 'update' : 'create'}`, { id: selected[kind] || undefined, data })
    selected[kind] = result.id
    if (kind === 'provider' && secret.value) {
      await runtimeRequest('provider.credential.set', { providerId: result.id, secret: secret.value })
      secret.value = ''
    }
    await load(); message.success('配置已保存，将在下一轮运行生效')
  } catch (error) { message.error(String(error)) }
  finally { busy.value = false }
}
async function setDefault(value: string) {
  try { await runtimeRequest('settings.update', { defaultWorkflowId: value }); defaultWorkflow.value = value }
  catch (error) { message.error(String(error)) }
}
</script>

<template>
  <NCard title="模型与 Agent 协作">
    <NFormItem label="新会话默认流程"><NSelect :value="defaultWorkflow" :options="options(workflows.filter((item) => item.enabled))" @update:value="setDefault" /></NFormItem>
    <NCollapse>
      <NCollapseItem title="供应商与凭据" name="provider">
        <NFormItem label="编辑已有供应商；清空后新建"><NSelect clearable :value="selected.provider || null" :options="options(providers)" @update:value="editProvider" /></NFormItem>
        <NFormItem label="名称"><NInput v-model:value="provider.name" /></NFormItem>
        <NFormItem label="API 地址"><NInput v-model:value="provider.base_url" /></NFormItem>
        <NFormItem label="凭据引用"><NInput v-model:value="provider.credential_ref" placeholder="env:环境变量名" /></NFormItem>
        <NFormItem label="API Key（留空保留，填写后保存到系统钥匙串）"><NInput v-model:value="secret" type="password" autocomplete="off" /></NFormItem>
        <div class="actions"><NCheckbox v-model:checked="provider.enabled">启用</NCheckbox><NButton :loading="busy" @click="save('provider')">保存供应商</NButton></div>
      </NCollapseItem>
      <NCollapseItem title="模型配置" name="model">
        <NFormItem label="编辑已有模型；清空后新建"><NSelect clearable :value="selected.model || null" :options="options(models)" @update:value="editModel" /></NFormItem>
        <NFormItem label="配置名称"><NInput v-model:value="model.name" /></NFormItem>
        <NFormItem label="供应商"><NSelect v-model:value="model.provider_id" :options="options(providers)" /></NFormItem>
        <NFormItem label="模型名称"><NInput v-model:value="model.model_name" /></NFormItem>
        <NFormItem label="上下文预算"><NInputNumber v-model:value="model.context_budget_tokens" :min="1" /></NFormItem>
        <NFormItem label="Temperature"><NInputNumber v-model:value="temperature" :min="0" :max="2" :step="0.1" /></NFormItem>
        <div class="actions"><NCheckbox v-model:checked="imageInput">支持图片输入</NCheckbox><NCheckbox v-model:checked="model.enabled">启用</NCheckbox><NButton :loading="busy" @click="save('model')">保存模型</NButton></div>
      </NCollapseItem>
      <NCollapseItem title="Agent 角色" name="agent">
        <NFormItem label="编辑已有 Agent；清空后新建"><NSelect clearable :value="selected.agent || null" :options="options(agents)" @update:value="editAgent" /></NFormItem>
        <NFormItem label="角色名称"><NInput v-model:value="agent.name" /></NFormItem>
        <NFormItem label="使用模型"><NSelect v-model:value="agent.model_config_id" :options="options(models)" /></NFormItem>
        <NFormItem label="职责与指令"><NInput v-model:value="agent.instructions" type="textarea" :autosize="{ minRows: 3, maxRows: 10 }" /></NFormItem>
        <NFormItem label="允许工具（逗号分隔，* 表示全部，留空禁用）"><NInput v-model:value="allowedTools" /></NFormItem>
        <div class="actions"><NCheckbox v-model:checked="agent.enabled">启用</NCheckbox><NButton :loading="busy" @click="save('agent')">保存 Agent</NButton></div>
      </NCollapseItem>
      <NCollapseItem title="预设协作流程" name="workflow">
        <NFormItem label="编辑已有流程；清空后新建"><NSelect clearable :value="selected.workflow || null" :options="options(workflows)" @update:value="editWorkflow" /></NFormItem>
        <NFormItem label="流程名称"><NInput v-model:value="workflow.name" /></NFormItem>
        <NFormItem label="流程类型"><NSelect v-model:value="workflow.kind" :options="[{ label: '单 Agent', value: 'single' }, { label: '前端编写与视觉审查', value: 'frontend-review' }]" /></NFormItem>
        <NFormItem label="编写 Agent"><NSelect v-model:value="workflow.code_agent_id" :options="options(agents)" /></NFormItem>
        <template v-if="workflow.kind === 'frontend-review'">
          <NFormItem label="视觉审查 Agent"><NSelect v-model:value="workflow.review_agent_id" :options="options(agents)" /></NFormItem>
          <NFormItem label="最多自动返修轮次"><NInputNumber v-model:value="workflow.max_revisions" :min="0" :max="2" /></NFormItem>
        </template>
        <div class="actions"><NCheckbox v-model:checked="workflow.enabled">启用</NCheckbox><NButton :loading="busy" @click="save('workflow')">保存流程</NButton></div>
      </NCollapseItem>
    </NCollapse>
  </NCard>
</template>

<style scoped>
.actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}
</style>
