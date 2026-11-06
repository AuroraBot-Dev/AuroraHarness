<script setup lang="ts">
import { NAlert, NButton, NInput, useDialog, useMessage } from 'naive-ui'
import {
  ArrowUpRight,
  Copy,
  Download,
  Edit,
  FileText,
  Plus,
  Search,
  Trash,
  Upload,
} from '~/utils/icons'
import type { PromptTemplate } from '~/types/prompt'
import { promptVariables } from '~/utils/prompts'

const store = usePromptStore()
const message = useMessage()
const dialog = useDialog()
const query = ref('')
const editing = ref(false)
const using = ref(false)
const selected = ref<PromptTemplate | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const importing = ref(false)
const filtered = computed(() =>
  store.items.filter((item) =>
    `${item.name} ${item.description} ${item.content}`
      .toLowerCase()
      .includes(query.value.toLowerCase()),
  ),
)

useHead({ title: '提示词库' })
onMounted(() => store.hydrate())

function edit(template: PromptTemplate | null = null) {
  selected.value = template
  editing.value = true
}

function useTemplate(template: PromptTemplate) {
  selected.value = template
  using.value = true
}

function duplicate(template: PromptTemplate) {
  try {
    const copy = store.duplicate(template.id)
    edit(copy)
  } catch (error) {
    message.error(String(error))
  }
}

function remove(template: PromptTemplate) {
  dialog.warning({
    title: '删除提示词？',
    content: `将删除「${template.name}」。已填入的任务和 Agent Prompt 不受影响。`,
    positiveText: '删除',
    negativeText: '取消',
    onPositiveClick: () => {
      try {
        store.remove(template.id)
      } catch (error) {
        message.error(String(error))
        return false
      }
    },
  })
}

function exportCollection() {
  const url = URL.createObjectURL(
    new Blob([store.exportCollection()], { type: 'application/json' }),
  )
  const link = document.createElement('a')
  link.href = url
  link.download = 'aurora-prompts.json'
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

async function importCollection(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  importing.value = true
  try {
    if (file.size > 2000000) throw new Error('导入文件不能超过 2 MB')
    const count = store.importCollection(await file.text())
    message.success(`已导入 ${count} 条提示词，原有内容已保留`)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    importing.value = false
    input.value = ''
  }
}
</script>

<template>
  <main class="library-page app-scrollbar">
    <PageHeader
      title="提示词库"
      description="让一次好表达，成为每一次的好起点。保存、调整、组合你的工作方式。"
    >
      <NButton
        type="primary"
        :disabled="!store.hydrated"
        @click="edit()"
      >
        <template #icon><AppIcon :component="Plus" /></template>
        新建提示词
      </NButton>
    </PageHeader>
    <div class="library-content">
      <NAlert
        v-if="store.error"
        type="warning"
        title="提示词库暂不可用"
      >
        {{ store.error }}。原有数据已保留，请恢复有效的本地存储后重试。
        <NButton
          size="small"
          @click="store.hydrate"
        >
          重试读取
        </NButton>
      </NAlert>
      <div class="library-toolbar">
        <NInput
          v-model:value="query"
          :input-props="{ 'aria-label': '搜索提示词' }"
          placeholder="搜索你的提示词"
          clearable
        >
          <template #prefix>
            <AppIcon
              :component="Search"
              :size="16"
            />
          </template>
        </NInput>
        <div class="toolbar-actions">
          <input
            ref="fileInput"
            type="file"
            accept=".json,application/json"
            hidden
            @change="importCollection"
          />
          <NButton
            quaternary
            :loading="importing"
            :disabled="!store.hydrated"
            @click="fileInput?.click()"
          >
            <template #icon>
              <AppIcon
                :component="Upload"
                :size="16"
              />
            </template>
            导入
          </NButton>
          <NButton
            quaternary
            :disabled="!store.hydrated || !store.items.length"
            @click="exportCollection"
          >
            <template #icon>
              <AppIcon
                :component="Download"
                :size="16"
              />
            </template>
            导出
          </NButton>
        </div>
      </div>
      <div
        v-if="!filtered.length && store.hydrated"
        class="empty-library"
      >
        <AppIcon
          :component="FileText"
          :size="36"
        />
        <h2>{{ query ? '还没有找到它' : '留住你的第一个好提示词' }}</h2>
        <p>
          {{
            query ? '试试其他关键词，或清空搜索。' : '从一段常用指令开始，慢慢建立自己的提示词库。'
          }}
        </p>
        <NButton
          v-if="!query"
          secondary
          @click="edit()"
        >
          新建提示词
        </NButton>
        <NButton
          v-else
          secondary
          @click="query = ''"
        >
          清空搜索
        </NButton>
      </div>
      <TransitionGroup
        name="library-card"
        tag="div"
        class="prompt-grid"
      >
        <article
          v-for="(item, index) in filtered"
          :key="item.id"
          class="prompt-card"
        >
          <div class="prompt-card-top">
            <span class="prompt-icon">
              <AppIcon
                :component="FileText"
                :size="21"
              />
            </span>
            <span class="prompt-index">{{ String(index + 1).padStart(2, '0') }}</span>
          </div>
          <h2>{{ item.name }}</h2>
          <p class="prompt-description">{{ item.description || '你的自定义提示词' }}</p>
          <p class="prompt-excerpt">{{ item.content }}</p>
          <div class="prompt-meta">
            <span>
              {{
                promptVariables(item.content).length
                  ? `${promptVariables(item.content).length} 个可填写变量`
                  : '即取即用'
              }}
            </span>
            <span>{{ item.content.length }} 字符</span>
          </div>
          <footer class="prompt-actions">
            <NButton
              text
              type="primary"
              @click="useTemplate(item)"
            >
              使用提示词
              <template #icon>
                <AppIcon
                  :component="ArrowUpRight"
                  :size="16"
                />
              </template>
            </NButton>
            <div class="card-tools">
              <NButton
                quaternary
                circle
                size="small"
                aria-label="编辑提示词"
                title="编辑"
                @click="edit(item)"
              >
                <template #icon>
                  <AppIcon
                    :component="Edit"
                    :size="16"
                  />
                </template>
              </NButton>
              <NButton
                quaternary
                circle
                size="small"
                aria-label="复制提示词"
                title="复制"
                @click="duplicate(item)"
              >
                <template #icon>
                  <AppIcon
                    :component="Copy"
                    :size="16"
                  />
                </template>
              </NButton>
              <NButton
                quaternary
                circle
                size="small"
                aria-label="删除提示词"
                title="删除"
                @click="remove(item)"
              >
                <template #icon>
                  <AppIcon
                    :component="Trash"
                    :size="16"
                  />
                </template>
              </NButton>
            </div>
          </footer>
        </article>
      </TransitionGroup>
      <p class="library-note">
        {{ store.items.length }} / 100 条 · 保存在当前浏览器或桌面应用的本地存储中。可导出 JSON
        备份，也可导入其他设备。
      </p>
    </div>
    <PromptEditor
      v-model:show="editing"
      :template="selected"
    />
    <PromptUse
      v-model:show="using"
      :template="selected"
    />
  </main>
</template>

<style scoped>
.prompt-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(280px, 100%), 1fr));
  gap: 18px;
}

.prompt-card {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 24px;
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
}

.prompt-card-top,
.prompt-meta,
.prompt-actions,
.card-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.prompt-icon {
  display: grid;
  width: 42px;
  height: 42px;
  place-items: center;
  border-radius: 13px;
  background: var(--accent-soft);
  color: var(--accent);
}

.prompt-index {
  color: var(--text-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}

.prompt-card h2 {
  margin: 24px 0 8px;
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.025em;
  overflow-wrap: anywhere;
}

.prompt-description {
  min-height: 40px;
  margin: 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.7;
  overflow-wrap: anywhere;
}

.prompt-excerpt {
  display: -webkit-box;
  min-height: 88px;
  margin: 20px 0;
  padding: 12px;
  border-radius: 10px;
  background: var(--app-bg);
  color: var(--text-muted);
  font-size: 11px;
  line-height: 1.9;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
}

.prompt-meta {
  gap: 8px;
  margin: auto 0 18px;
  color: var(--text-muted);
  font-size: 10px;
}

.prompt-actions {
  gap: 8px;
  padding-top: 14px;
  border-top: 1px solid var(--border);
}

.card-tools {
  gap: 2px;
}
</style>
