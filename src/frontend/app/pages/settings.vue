<script setup lang="ts">
import {
  NAlert,
  NButton,
  NCard,
  NDescriptions,
  NDescriptionsItem,
  NFormItem,
  NInput,
  NTag,
  useMessage,
} from 'naive-ui'
import { PlugConnected, Refresh } from '~/utils/icons'
import type { McpPackage } from '~/types/agent'

const runtime = useRuntimeStore()
const message = useMessage()
const route = useRoute()
const section = ref('appearance')
const sections = [
  { id: 'appearance', label: '外观' },
  { id: 'collaboration', label: '模型与协作' },
  { id: 'tools', label: '工具连接' },
  { id: 'runtime', label: '运行时' },
]
watch(
  () => route.query.model,
  (id) => {
    if (typeof id === 'string' && id) section.value = 'collaboration'
  },
  { immediate: true },
)
const restarting = ref(false)
const connecting = ref('')
const packageConfig = reactive<Record<string, string>>({})
const instanceNames = reactive<Record<string, string>>({})
const serverConfig = ref('{\n  "name": "custom",\n  "command": "uvx",\n  "args": []\n}')
useHead({ title: '设置' })

onMounted(() => runtime.loadMcp().catch((error) => message.error(String(error))))

function defaultConfig(pkg: McpPackage) {
  const raw = pkg.configSchema.properties
  const properties = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {}
  return Object.fromEntries(
    Object.entries(properties).flatMap(([key, value]) =>
      value && typeof value === 'object' && Object.hasOwn(value, 'default')
        ? [[key, (value as Record<string, unknown>).default]]
        : [],
    ),
  )
}

function configText(pkg: McpPackage) {
  if (!packageConfig[pkg.id]) packageConfig[pkg.id] = JSON.stringify(defaultConfig(pkg), null, 2)
  return packageConfig[pkg.id] ?? '{}'
}

async function connectPackage(pkg: McpPackage) {
  connecting.value = pkg.id
  try {
    await runtime.connectPackage(
      pkg.id,
      instanceNames[pkg.id] || pkg.id,
      JSON.parse(packageConfig[pkg.id] ?? configText(pkg)),
    )
    message.success(`${pkg.name} 已连接；新建会话后可使用其工具`)
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    connecting.value = ''
  }
}

async function connectServer() {
  connecting.value = 'server'
  try {
    await runtime.connectServer(JSON.parse(serverConfig.value))
    message.success('MCP Server 已连接')
  } catch (error) {
    message.error(error instanceof Error ? error.message : String(error))
  } finally {
    connecting.value = ''
  }
}

async function restart() {
  restarting.value = true
  try {
    await runtime.restart()
    await runtime.loadMcp()
    message.success('运行时已重启，历史已恢复')
  } catch (error) {
    message.error(String(error))
  } finally {
    restarting.value = false
  }
}
</script>

<template>
  <main class="library-page app-scrollbar">
    <PageHeader
      title="设置"
      description="按你的方式，调整 Aurora。"
    />
    <nav
      class="settings-sections"
      aria-label="设置分类"
    >
      <button
        v-for="item in sections"
        :key="item.id"
        :aria-pressed="section === item.id"
        @click="section = item.id"
      >
        {{ item.label }}
      </button>
    </nav>
    <div class="settings-grid">
      <AppearanceSettings v-if="section === 'appearance'" />

      <NCard
        v-if="section === 'runtime'"
        title="Aurora 运行时"
      >
        <NDescriptions
          label-placement="left"
          :column="1"
          size="small"
        >
          <NDescriptionsItem label="连接">
            <NTag
              size="small"
              :bordered="false"
            >
              {{ runtime.connectionStatus }}
            </NTag>
          </NDescriptionsItem>
          <NDescriptionsItem label="协议">v{{ runtime.info.version }}</NDescriptionsItem>
          <NDescriptionsItem label="传输">{{ runtime.info.mode }}</NDescriptionsItem>
          <NDescriptionsItem label="接口数">
            {{ runtime.info.capabilities?.length || 0 }}
          </NDescriptionsItem>
        </NDescriptions>
        <NAlert
          type="info"
          :show-icon="false"
          class="runtime-note"
        >
          历史与协作配置保存在本机数据库：{{ runtime.info.databasePath }}
        </NAlert>
        <NButton
          secondary
          :loading="restarting"
          @click="restart"
        >
          <template #icon><AppIcon :component="Refresh" /></template>
          重启运行时
        </NButton>
      </NCard>

      <WorkflowSettings v-if="section === 'collaboration'" />

      <NCard
        v-if="section === 'tools'"
        title="MCP 功能包"
      >
        <template #header-extra>
          <AppIcon
            :component="PlugConnected"
            :size="18"
          />
        </template>
        <NAlert
          v-if="Object.keys(runtime.pluginErrors).length"
          type="warning"
          :show-icon="false"
        >
          {{ runtime.pluginErrors }}
        </NAlert>
        <article
          v-for="pkg in runtime.packages"
          :key="pkg.id"
          class="package-card"
        >
          <div class="package-head">
            <div>
              <strong>{{ pkg.name }}</strong>
              <span>v{{ pkg.version }}</span>
              <p>{{ pkg.description }}</p>
            </div>
            <NTag
              v-if="runtime.connections.some((item) => item.packageId === pkg.id)"
              type="success"
              size="small"
            >
              已连接
            </NTag>
          </div>
          <div
            v-if="!runtime.connections.some((item) => item.packageId === pkg.id)"
            class="package-form"
          >
            <NFormItem label="实例名">
              <NInput
                v-model:value="instanceNames[pkg.id]"
                :placeholder="pkg.id"
              />
            </NFormItem>
            <NFormItem label="配置 JSON">
              <NInput
                :value="configText(pkg)"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 8 }"
                @update:value="packageConfig[pkg.id] = $event"
              />
            </NFormItem>
            <NButton
              type="primary"
              :loading="connecting === pkg.id"
              @click="connectPackage(pkg)"
            >
              连接
            </NButton>
          </div>
          <div
            v-else
            class="connections"
          >
            <div
              v-for="item in runtime.connections.filter((entry) => entry.packageId === pkg.id)"
              :key="item.name"
            >
              <span>{{ item.name }} · {{ item.tools.length }} 个工具</span>
              <NButton
                size="tiny"
                secondary
                type="error"
                @click="runtime.disconnectPackage(item.name)"
              >
                断开
              </NButton>
            </div>
          </div>
        </article>
      </NCard>

      <NCard
        v-if="section === 'tools'"
        title="底层 MCP Server"
      >
        <NFormItem label="Server 配置 JSON">
          <NInput
            v-model:value="serverConfig"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
          />
        </NFormItem>
        <NButton
          type="primary"
          :loading="connecting === 'server'"
          @click="connectServer"
        >
          连接 Server
        </NButton>
        <div class="connections">
          <div
            v-for="item in runtime.serverConnections.filter((entry) => !entry.packageId)"
            :key="item.name"
          >
            <span>{{ item.name }} · {{ item.tools.length }} 个工具</span>
            <NButton
              size="tiny"
              secondary
              type="error"
              @click="runtime.disconnectServer(item.name)"
            >
              断开
            </NButton>
          </div>
        </div>
      </NCard>
    </div>
  </main>
</template>

<style scoped>
.settings-sections {
  display: flex;
  flex-wrap: wrap;
  width: min(1120px, 100%);
  gap: 4px;
  margin: 0 auto 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.settings-sections button {
  padding: 8px 14px;
  border: 0;
  border-radius: 8px;
  background: transparent;
  color: var(--text-muted);
  font-size: 12px;
  cursor: pointer;
}

.settings-sections button[aria-pressed='true'] {
  color: var(--text);
  background: var(--surface-muted);
}

.settings-grid {
  display: grid;
  width: min(1120px, 100%);
  gap: 20px;
  margin: 0 auto;
}

.settings-grid :deep(.n-card) {
  border: 1px solid var(--border);
  border-radius: 20px;
  background: var(--surface);
}

.settings-grid :deep(.n-card-header__main) {
  font-size: 16px;
  font-weight: 600;
  letter-spacing: -0.02em;
}

.runtime-note {
  margin: 18px 0;
  overflow-wrap: anywhere;
}

.package-card {
  padding: 18px 0;
  border-bottom: 1px solid var(--border);
}

.package-card:last-child {
  border-bottom: 0;
}

.package-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.package-head strong {
  font-size: 13px;
}

.package-head span {
  margin-left: 8px;
  color: var(--text-muted);
  font-size: 11px;
}

.package-head p {
  margin: 6px 0 0;
  color: var(--text-muted);
  font-size: 12px;
  line-height: 1.8;
}

.package-form {
  margin-top: 16px;
  padding: 18px;
  border-radius: 12px;
  background: var(--app-bg);
}

.connections {
  display: grid;
  gap: 10px;
  margin-top: 14px;
}

.connections > div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: var(--text-muted);
  font-size: 12px;
}

.connections span {
  overflow-wrap: anywhere;
}
</style>
