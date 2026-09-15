<script setup lang="ts">
import {
  NAlert,
  NButton,
  NDrawer,
  NDrawerContent,
  NEmpty,
  NIcon,
  NSpin,
  NTabPane,
  NTabs,
  useDialog,
  useMessage,
} from 'naive-ui'
import { ArrowBackUp, FileDiff, Refresh } from '@vicons/tabler'
import type { GitChange, GitView, SessionRecord } from '~/types/agent'

const props = defineProps<{ session: SessionRecord }>()
const show = defineModel<boolean>('show', { required: true })
const git = useGitStore()
const dialog = useDialog()
const message = useMessage()
const view = ref<GitView>('run')
const selectedPath = ref('')
const latestRun = computed(() => [...props.session.runs].reverse().find((run) => ['completed', 'failed'].includes(run.status)))
const runId = computed(() => view.value === 'run' ? latestRun.value?.id : undefined)
const status = computed(() => git.statusFor(props.session.id, view.value, runId.value))
const selectedDiff = computed(() => selectedPath.value ? git.diffFor(props.session.id, view.value, selectedPath.value, runId.value) : undefined)
const lines = computed(() => (selectedDiff.value?.content ?? '').split('\n'))

function lineClass(line: string) {
  if (line.startsWith('@@')) return 'hunk'
  if (line.startsWith('+') && !line.startsWith('+++')) return 'addition'
  if (line.startsWith('-') && !line.startsWith('---')) return 'deletion'
  if (line.startsWith('diff ') || line.startsWith('index ') || line.startsWith('---') || line.startsWith('+++')) return 'meta'
  return 'context'
}

function statusLabel(change: GitChange) {
  return ({ A: '新增', M: '修改', D: '删除', R: '重命名', C: '复制', T: '类型', U: '冲突' } as Record<string, string>)[change.status] ?? change.status
}

async function refresh() {
  if (view.value === 'run' && !runId.value) return
  selectedPath.value = ''
  try {
    const result = await git.loadStatus(props.session.id, view.value, runId.value)
    if (result.files[0]) await selectFile(result.files[0].path)
  } catch { /* store 已保留可展示错误 */ }
}

async function selectFile(path: string) {
  selectedPath.value = path
  try { await git.loadDiff(props.session.id, view.value, path, runId.value) }
  catch { /* store 已保留可展示错误 */ }
}

function confirmRollback() {
  if (!runId.value || !status.value?.canRollback) return
  dialog.warning({
    title: '回滚本轮改动',
    content: '将恢复到本轮 Agent 运行开始前的状态。运行前已有的未提交改动会保留。',
    positiveText: '确认回滚',
    negativeText: '取消',
    async onPositiveClick() {
      try {
        await git.rollback(props.session.id, runId.value!)
        message.success('本轮改动已回滚')
        await refresh()
        await git.loadStatus(props.session.id, 'workspace')
      } catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
    },
  })
}

watch(show, (opened) => {
  if (!opened) return
  if (!latestRun.value) view.value = 'workspace'
  else refresh()
})
watch(view, refresh)
watch(() => props.session.updatedAt, () => { if (show.value) refresh() })
</script>

<template>
  <NDrawer v-model:show="show" :width="760" placement="right" class="git-drawer">
    <NDrawerContent closable>
      <template #header>
        <div class="drawer-title"><NIcon :component="FileDiff" /><span>项目改动</span></div>
      </template>

      <div class="git-toolbar">
        <NTabs v-model:value="view" type="segment" size="small">
          <NTabPane name="run" tab="本轮改动" :disabled="!latestRun" />
          <NTabPane name="workspace" tab="工作区改动" />
        </NTabs>
        <NButton quaternary circle size="small" :loading="git.loading" aria-label="刷新改动" @click="refresh">
          <template #icon><NIcon :component="Refresh" /></template>
        </NButton>
      </div>

      <NAlert v-if="git.error" type="error" :show-icon="false" class="git-alert">{{ git.error }}</NAlert>
      <div v-if="status" class="repo-summary">
        <span>{{ status.branch }}</span>
        <code>{{ status.unborn ? '尚无提交' : status.head?.slice(0, 8) }}</code>
        <span>{{ status.files.length }} 个文件</span>
      </div>

      <div v-if="view === 'run' && status" class="rollback-row">
        <NButton
          size="small"
          type="warning"
          secondary
          :disabled="!status.canRollback"
          :loading="git.rollingBack"
          @click="confirmRollback"
        >
          <template #icon><NIcon :component="ArrowBackUp" /></template>
          回滚本轮
        </NButton>
        <span v-if="!status.canRollback">{{ status.rollbackReason }}</span>
      </div>

      <div v-if="git.loading && !status" class="drawer-loading"><NSpin />正在读取 Git 状态</div>
      <NEmpty v-else-if="status && !status.files.length" description="没有检测到改动" class="drawer-empty" />
      <div v-else-if="status" class="changes-layout">
        <aside class="file-list app-scrollbar">
          <button
            v-for="file in status.files"
            :key="file.path"
            class="file-row"
            :class="{ active: selectedPath === file.path }"
            @click="selectFile(file.path)"
          >
            <span class="file-state" :class="file.status.toLowerCase()">{{ file.status }}</span>
            <span class="file-name" :title="file.path">{{ file.path }}</span>
            <span class="file-stats">
              <i v-if="file.staged">S</i><i v-if="file.untracked">U</i>
              <b>+{{ file.additions }}</b><em>-{{ file.deletions }}</em>
            </span>
            <small>{{ statusLabel(file) }}<template v-if="file.oldPath"> · {{ file.oldPath }}</template></small>
          </button>
        </aside>

        <section class="diff-panel app-scrollbar">
          <div v-if="git.diffLoading" class="drawer-loading"><NSpin size="small" />正在生成 diff</div>
          <template v-else-if="selectedDiff">
            <header class="diff-header"><strong>{{ selectedDiff.path }}</strong><span v-if="selectedDiff.truncated">内容过长，已截断</span></header>
            <div v-if="selectedDiff.binary" class="binary-notice">二进制文件已发生变化，无法显示文本差异。</div>
            <pre v-else class="diff-content"><code><span v-for="(line, index) in lines" :key="index" :class="lineClass(line)">{{ line || ' ' }}</span></code></pre>
          </template>
          <NEmpty v-else description="选择一个文件查看差异" class="drawer-empty" />
        </section>
      </div>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>
.drawer-title{display:flex;align-items:center;gap:8px}.git-toolbar{display:flex;align-items:center;gap:10px}.git-toolbar .n-tabs{flex:1}.git-alert{margin-top:12px}.repo-summary{display:flex;align-items:center;gap:10px;margin:14px 0 8px;color:var(--text-muted);font-size:12px}.repo-summary code{padding:2px 6px;border-radius:5px;background:var(--surface-muted)}.repo-summary span:last-child{margin-left:auto}.rollback-row{display:flex;min-height:36px;align-items:center;gap:10px;margin-bottom:10px}.rollback-row span{color:var(--text-muted);font-size:11px}.drawer-loading{display:flex;min-height:180px;align-items:center;justify-content:center;gap:9px;color:var(--text-muted);font-size:13px}.drawer-empty{margin-top:80px}.changes-layout{display:grid;height:calc(100vh - 210px);min-height:360px;grid-template-columns:230px minmax(0,1fr);overflow:hidden;border:1px solid var(--border);border-radius:10px}.file-list{overflow:auto;border-right:1px solid var(--border);background:var(--surface)}.file-row{display:grid;width:100%;grid-template-columns:22px minmax(0,1fr) auto;gap:5px 7px;padding:10px;border:0;border-bottom:1px solid var(--border);background:transparent;color:var(--text);text-align:left;cursor:pointer}.file-row:hover,.file-row.active{background:var(--surface-muted)}.file-state{grid-row:1/3;color:#b7791f;font:700 11px/20px ui-monospace,monospace}.file-state.a{color:#16845b}.file-state.d{color:#c33f3f}.file-name{overflow:hidden;font-size:12px;text-overflow:ellipsis;white-space:nowrap}.file-row small{grid-column:2/4;overflow:hidden;color:var(--text-muted);font-size:10px;text-overflow:ellipsis;white-space:nowrap}.file-stats{display:flex;align-items:center;gap:4px;font-size:10px}.file-stats i{padding:1px 3px;border-radius:3px;background:#2563eb;color:white;font-style:normal}.file-stats b{color:#16845b}.file-stats em{color:#c33f3f;font-style:normal}.diff-panel{min-width:0;overflow:auto;background:var(--surface)}.diff-header{position:sticky;top:0;z-index:1;display:flex;align-items:center;justify-content:space-between;padding:10px 12px;border-bottom:1px solid var(--border);background:var(--surface);font-size:12px}.diff-header span{color:#b7791f;font-size:10px}.diff-content{min-width:max-content;margin:0;padding:8px 0;font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}.diff-content code,.diff-content span{display:block}.diff-content span{min-height:19px;padding:0 12px;white-space:pre}.diff-content .addition{background:rgb(22 132 91 / 13%);color:#16845b}.diff-content .deletion{background:rgb(195 63 63 / 12%);color:#c33f3f}.diff-content .hunk{background:rgb(37 99 235 / 10%);color:#2563eb}.diff-content .meta{color:var(--text-muted)}.binary-notice{padding:28px;color:var(--text-muted);font-size:13px;text-align:center}@media(max-width:700px){.changes-layout{grid-template-columns:1fr;grid-template-rows:minmax(120px,32%) minmax(0,1fr)}.file-list{border-right:0;border-bottom:1px solid var(--border)}}
</style>
