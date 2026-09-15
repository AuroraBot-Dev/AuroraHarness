<script setup lang="ts">
import { NAlert, NButton, NTag } from 'naive-ui'
import { onRuntimeEvent, runtimeRequest } from '~/utils/runtimeClient'
import type { RunDetail } from '~/types/workflow'
const route = useRoute()
const props = defineProps<{ runId: string; status: string }>()
const detail = ref<RunDetail | null>(null)
const error = ref('')
const images = reactive<Record<string, string>>({})
const stage = ref('')
const labels: Record<string, string> = { code: '编写代码', capture: '生成截图', review: '视觉审查', summary: '汇总结果' }
const verdicts: Record<string, string> = { passed: '审查通过', changes_requested: '需要返修', unable_to_review: '无法完成审查' }
let unsubscribe: (() => void) | undefined
async function load() {
  try { detail.value = await runtimeRequest<RunDetail>('run.get', { runId: props.runId }); error.value = '' }
  catch (reason) { error.value = String(reason) }
}
async function showImage(id: string) {
  if (images[id]) return
  try {
    const artifact = await runtimeRequest<{ base64: string; mediaType: string }>('artifact.get', { artifactId: id })
    images[id] = `data:${artifact.mediaType};base64,${artifact.base64}`
  } catch (reason) { error.value = String(reason) }
}
onMounted(() => {
  if (!['running', 'queued'].includes(props.status)) void load()
  unsubscribe = onRuntimeEvent((event) => {
    if (event.run_id !== props.runId) return
    if (event.type.startsWith('stage.')) stage.value = `${labels[String(event.payload.stepKey)] || event.payload.stepKey} · 第 ${Number(event.payload.iteration || 0) + 1} 轮 · ${event.type.endsWith('completed') ? '已完成' : '进行中'}`
  })
})
onBeforeUnmount(() => unsubscribe?.())
watch(() => props.status, (value) => { if (!['running', 'queued'].includes(value)) void load() })
</script>

<template>
  <div v-if="stage || detail?.agentRuns.length || error" class="run-review">
    <p v-if="stage" class="stage">{{ stage }}</p>
    <NAlert v-if="error" type="error">{{ error }}</NAlert>
    <details v-if="detail?.agentRuns.length" :open="!!route.hash">
      <summary>Agent 执行记录 · {{ detail.agentRuns.length }} 个阶段</summary>
      <article v-for="item in detail.agentRuns" :id="`agent-run-${item.id}`" :key="item.id" class="execution">
        <strong>{{ item.parentTaskId ? '委派任务' : labels[item.stepKey] || item.stepKey }} · 第 {{ item.iteration + 1 }} 轮</strong>
        <span>{{ item.status }} · {{ item.agentName || item.agentId }} · {{ item.modelName }}</span>
        <details><summary>阶段输出</summary><MarkdownContent :content="item.output || item.error" /></details>
      </article>
    </details>
    <article v-for="review in detail?.reviews" :key="review.id" class="review">
      <NTag :type="review.verdict === 'passed' ? 'success' : 'warning'">{{ verdicts[review.verdict] }} · 第 {{ review.iteration + 1 }} 轮</NTag>
      <p>{{ review.summary }}</p>
      <div v-for="finding in review.findings" :key="finding.id" class="finding">
        <strong>{{ finding.severity === 'blocking' ? '需修复' : '建议' }}：{{ finding.description }}</strong>
        <p>{{ finding.location }} · {{ finding.suggestion }}</p>
        <NButton size="tiny" @click="showImage(finding.artifactId)">查看问题截图</NButton>
      </div>
      <div v-for="id in review.artifactIds" :key="id" class="evidence">
        <NButton v-if="!images[id]" size="small" @click="showImage(id)">加载审查截图</NButton>
        <img v-else :src="images[id]" alt="本轮视觉审查证据截图" loading="lazy">
      </div>
    </article>
  </div>
</template>

<style scoped>
.run-review{margin:16px 0;color:var(--text-muted);font-size:13px}.stage{font-weight:600}.execution,.review{padding:12px;margin:10px 0;border:1px solid var(--border);border-radius:8px}.execution>span{display:block;margin:5px 0;font-size:11px;overflow-wrap:anywhere}.finding{margin:10px 0;padding:10px;background:var(--surface-muted);border-radius:6px}.evidence{margin-top:10px}.evidence img{max-width:100%;max-height:650px;object-fit:contain}summary{cursor:pointer}
</style>
