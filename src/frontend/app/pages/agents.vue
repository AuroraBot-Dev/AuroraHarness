<script setup lang="ts">
import { NAlert, NButton, NCard, NCheckbox, NDrawer, NDrawerContent, NEmpty, NFormItem, NInput, NSelect, NSpace, NTag, useMessage } from 'naive-ui'
import type { AgentConfig, ModelType } from '~/types/workflow'
import { onRuntimeEvent } from '~/utils/runtimeClient'

const store = useAgentStore()
const message = useMessage()
const editing = ref(false)
const saving = ref(false)
const testing = ref('')
const selectedId = ref('')
const query = ref('')
const toolsText = ref('')
const form = reactive({ name: '', description: '', instructions: '', model_config_id: '', enabled: true, callable: false })
const labels: Record<ModelType, string> = { text: '文本', multimodal: '多模态 · 图片理解', embedding: '向量 · 暂不支持调用' }
const runLabels: Record<string, string> = { running: '运行中', waiting: '等待审批或补充信息', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断' }
const testLabels: Record<string, string> = { passed: '连接测试通过', failed: '连接测试失败', stale: '配置已变更，请重新测试' }
const filtered = computed(() => store.agents.filter((a) => `${a.name} ${a.description}`.toLowerCase().includes(query.value.toLowerCase())))
const modelOptions = computed(() => store.models.map((m) => ({ label: `${m.name} · ${labels[m.modelType]}`, value: m.id })))
const status = (id: string) => store.connected ? store.statuses.find((s) => s.id === id) : undefined
useHead({ title: 'Agent' })

function edit(agent?: AgentConfig) {
  selectedId.value = agent?.id ?? ''
  Object.assign(form, { name: agent?.name ?? '', description: agent?.description ?? '', instructions: agent?.instructions ?? '', model_config_id: agent?.modelConfigId ?? '', enabled: agent ? !!agent.enabled : true, callable: agent ? !!agent.callable : false })
  toolsText.value = agent?.allowedTools.join(', ') ?? ''
  editing.value = true
}

async function save() {
  saving.value = true
  try {
    await store.save(selectedId.value, { ...form, allowed_tools: toolsText.value.split(',').map((s) => s.trim()).filter(Boolean) })
    editing.value = false
    message.success('Agent 已保存，将在下一轮运行生效')
  } catch (error) { message.error(String(error)) }
  finally { saving.value = false }
}

async function toggle(agent: AgentConfig) {
  try { await store.save(agent.id, { enabled: !agent.enabled }) }
  catch (error) { message.error(String(error)) }
}

async function test(id: string) {
  testing.value = id
  try {
    const result = await store.test(id)
    if (result.status === 'passed') message.success('连接测试通过')
    else message.error(result.error ?? '连接测试失败')
  } catch (error) { message.error(String(error)) }
  finally { testing.value = '' }
}

let timer: ReturnType<typeof setInterval> | undefined
let unsubscribe: (() => void) | undefined
function refreshVisible() { if (!document.hidden) void store.refresh() }
onMounted(() => {
  refreshVisible()
  timer = setInterval(refreshVisible, 3000)
  document.addEventListener('visibilitychange', refreshVisible)
  unsubscribe = onRuntimeEvent((event) => { if (event.type === 'runtime.disconnected') store.disconnect() })
})
onBeforeUnmount(() => {
  clearInterval(timer)
  unsubscribe?.()
  document.removeEventListener('visibilitychange', refreshVisible)
})
</script>

<template>
  <main class="agents-page">
    <header class="page-heading">
      <div><h1>Agent</h1><p>配置专长、模型与工具，让主 Agent 按任务选择协作伙伴。</p></div>
      <NButton type="primary" @click="edit()">新建 Agent</NButton>
    </header>
    <NAlert v-if="store.error" type="warning" title="状态未知">{{ store.error }}</NAlert>
    <div class="toolbar"><NInput v-model:value="query" placeholder="搜索名称或能力" clearable /><NButton :loading="store.loading" @click="store.refresh">刷新</NButton><NuxtLink to="/settings">模型与供应商设置</NuxtLink></div>
    <NEmpty v-if="!filtered.length && !store.loading" :description="query ? '没有匹配的 Agent' : '暂无 Agent，创建一个协作伙伴开始使用'" />
    <div class="agent-grid">
      <NCard v-for="agent in filtered" :key="agent.id" :title="agent.name" class="agent-card">
        <NSpace size="small"><NTag :type="agent.enabled ? 'success' : 'default'" size="small">{{ agent.enabled ? '已启用' : '已停用' }}</NTag><NTag size="small">{{ labels[store.models.find((m) => m.id === agent.modelConfigId)?.modelType ?? 'text'] }}</NTag><NTag v-if="agent.callable" size="small" type="info">已开放调用</NTag></NSpace>
        <p class="description">{{ agent.description || '尚未填写能力描述' }}</p>
        <p class="model-name">模型：{{ store.models.find((m) => m.id === agent.modelConfigId)?.modelName ?? '未知' }}</p>
        <template v-if="status(agent.id)">
          <p :class="{ issue: status(agent.id)!.issues.length }">{{ status(agent.id)!.issues.join('；') || '配置完整' }}</p>
          <p>{{ status(agent.id)!.test.status ? testLabels[status(agent.id)!.test.status!] : '尚未测试连接' }}<span v-if="status(agent.id)!.test.testedAt"> · {{ new Date(status(agent.id)!.test.testedAt!).toLocaleString() }}</span></p>
          <p v-if="status(agent.id)!.test.error" class="issue">{{ status(agent.id)!.test.error }}</p>
          <p>运行中 {{ status(agent.id)!.runningCount }} · 等待处理 {{ status(agent.id)!.waitingCount }}</p>
          <p v-for="execution in status(agent.id)!.active" :key="execution.id"><NuxtLink :to="`/sessions/${execution.session_id}#agent-run-${execution.id}`">{{ runLabels[execution.status] }}：{{ execution.task }} →</NuxtLink></p>
          <NuxtLink v-if="status(agent.id)!.latest" :to="`/sessions/${status(agent.id)!.latest!.session_id}#agent-run-${status(agent.id)!.latest!.id}`">最近执行：{{ runLabels[status(agent.id)!.latest!.status] ?? status(agent.id)!.latest!.status }} →</NuxtLink>
          <p v-if="status(agent.id)!.latest" class="result-preview">{{ status(agent.id)!.latest!.error || status(agent.id)!.latest!.output || '任务正在处理' }}</p>
        </template>
        <p v-else>运行状态未知</p>
        <template #action><NSpace><NButton size="small" @click="edit(agent)">编辑</NButton><NButton size="small" @click="toggle(agent)">{{ agent.enabled ? '停用' : '启用' }}</NButton><NButton size="small" :loading="testing === agent.id" :disabled="!!testing || !store.connected || !!status(agent.id)?.issues.length" @click="test(agent.id)">测试连接</NButton></NSpace></template>
      </NCard>
    </div>
    <NDrawer v-model:show="editing" :width="560" :auto-focus="false" style="max-width: 100vw">
      <NDrawerContent :title="selectedId ? '编辑 Agent' : '新建 Agent'" closable>
        <NFormItem label="名称" required><NInput v-model:value="form.name" /></NFormItem>
        <NFormItem label="能力描述 · 告诉主 Agent 何时调用"><NInput v-model:value="form.description" type="textarea" placeholder="例如：理解页面截图，定位布局与视觉问题" :autosize="{ minRows: 2, maxRows: 5 }" /></NFormItem>
        <NFormItem label="模型" required><NSelect v-model:value="form.model_config_id" :options="modelOptions" /></NFormItem>
        <NAlert v-if="!store.models.length" type="info">请先在设置中配置模型。</NAlert>
        <NFormItem label="执行 Prompt"><NInput v-model:value="form.instructions" type="textarea" placeholder="描述职责、执行步骤和输出要求" :autosize="{ minRows: 6, maxRows: 16 }" /></NFormItem>
        <NFormItem label="允许工具 · 逗号分隔，* 为全部，留空仅模型推理"><NInput v-model:value="toolsText" placeholder="read_file, list_files" /></NFormItem>
        <NSpace vertical><NCheckbox v-model:checked="form.enabled">启用 Agent</NCheckbox><NCheckbox v-model:checked="form.callable">允许主 Agent 调用</NCheckbox><p>修改在下一轮运行生效。子 Agent 继承当前任务的沙箱与审批规则。</p></NSpace>
        <template #footer><NButton type="primary" :loading="saving" :disabled="!form.name.trim() || !form.model_config_id" @click="save">保存 Agent</NButton></template>
      </NDrawerContent>
    </NDrawer>
  </main>
</template>

<style scoped>
.agents-page { height: 100%; overflow-y: auto; padding: 28px; max-width: 1400px; margin: 0 auto; width: 100%; box-sizing: border-box; }
.page-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
h1 { margin: 0 0 6px; font-size: 26px; }
p { color: var(--text-muted); overflow-wrap: anywhere; }
.toolbar { display: flex; align-items: center; gap: 12px; margin: 20px 0; }
.toolbar :deep(.n-input) { max-width: 380px; }
.toolbar a { white-space: nowrap; }
.agent-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; }
.description { color: var(--text); min-height: 42px; white-space: pre-wrap; }
.issue { color: #c85839; }
.result-preview { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; white-space: pre-wrap; }
@media (max-width: 640px) { .agents-page { height: 100%; overflow-y: auto; padding: 16px; } .toolbar { flex-wrap: wrap; } .agent-grid { grid-template-columns: 1fr; } }
</style>
