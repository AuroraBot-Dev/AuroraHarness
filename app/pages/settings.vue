<script setup lang="ts">
import { NAlert, NButton, NCard, NDescriptions, NDescriptionsItem, NFormItem, NIcon, NInput, NRadioButton, NRadioGroup, NTag, useMessage } from 'naive-ui'
import { DeviceDesktop, PlugConnected, Refresh } from '@vicons/tabler'
import type { McpPackage, ThemePreference } from '~/types/agent'

const runtime = useRuntimeStore()
const ui = useUiStore()
const message = useMessage()
const restarting = ref(false)
const connecting = ref('')
const packageConfig = reactive<Record<string, string>>({})
const instanceNames = reactive<Record<string, string>>({})
const serverConfig = ref('{\n  "name": "custom",\n  "command": "uvx",\n  "args": []\n}')
useHead({ title: '设置' })

onMounted(() => runtime.loadMcp().catch((error) => message.error(String(error))))

function defaultConfig(pkg: McpPackage) {
  const raw = pkg.configSchema.properties
  const properties = raw && typeof raw === 'object' ? raw as Record<string, unknown> : {}
  return Object.fromEntries(Object.entries(properties).flatMap(([key, value]) => value && typeof value === 'object' && Object.hasOwn(value, 'default') ? [[key, (value as Record<string, unknown>).default]] : []))
}

function configText(pkg: McpPackage) {
  if (!packageConfig[pkg.id]) packageConfig[pkg.id] = JSON.stringify(defaultConfig(pkg), null, 2)
  return packageConfig[pkg.id] ?? '{}'
}

async function connectPackage(pkg: McpPackage) {
  connecting.value = pkg.id
  try {
    await runtime.connectPackage(pkg.id, instanceNames[pkg.id] || pkg.id, JSON.parse(packageConfig[pkg.id] ?? configText(pkg)))
    message.success(`${pkg.name} 已连接；新建会话后可使用其工具`)
  } catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
  finally { connecting.value = '' }
}

async function connectServer() {
  connecting.value = 'server'
  try { await runtime.connectServer(JSON.parse(serverConfig.value)); message.success('MCP Server 已连接') }
  catch (error) { message.error(error instanceof Error ? error.message : String(error)) }
  finally { connecting.value = '' }
}

async function restart() {
  restarting.value = true
  try { await runtime.restart(); await runtime.loadMcp(); message.success('运行时已重启，历史已恢复') }
  catch (error) { message.error(String(error)) }
  finally { restarting.value = false }
}
</script>

<template>
  <main class="settings-page app-scrollbar">
    <header class="settings-header"><p>应用偏好与运行时</p><h1>设置</h1></header>
    <div class="settings-grid">
      <NCard title="外观"><template #header-extra><NIcon :component="DeviceDesktop" :size="18" /></template>
        <div class="setting-row"><div><strong>主题</strong><p>可跟随系统，也可锁定浅色或深色。</p></div><NRadioGroup :value="ui.theme" size="small" @update:value="ui.setTheme($event as ThemePreference)"><NRadioButton value="system">系统</NRadioButton><NRadioButton value="light">浅色</NRadioButton><NRadioButton value="dark">深色</NRadioButton></NRadioGroup></div>
      </NCard>

      <NCard title="Aurora 运行时">
        <NDescriptions label-placement="left" :column="1" size="small">
          <NDescriptionsItem label="连接"><NTag size="small" :bordered="false">{{ runtime.connectionStatus }}</NTag></NDescriptionsItem>
          <NDescriptionsItem label="协议">v{{ runtime.info.version }}</NDescriptionsItem>
          <NDescriptionsItem label="传输">{{ runtime.info.mode }}</NDescriptionsItem>
          <NDescriptionsItem label="接口数">{{ runtime.info.capabilities?.length || 0 }}</NDescriptionsItem>
        </NDescriptions>
        <NAlert type="info" :show-icon="false" class="runtime-note">历史与协作配置保存在本机数据库：{{ runtime.info.databasePath }}</NAlert>
        <NButton secondary :loading="restarting" @click="restart"><template #icon><NIcon :component="Refresh" /></template>重启运行时</NButton>
      </NCard>

      <WorkflowSettings />

      <NCard title="MCP 功能包"><template #header-extra><NIcon :component="PlugConnected" :size="18" /></template>
        <NAlert v-if="Object.keys(runtime.pluginErrors).length" type="warning" :show-icon="false">{{ runtime.pluginErrors }}</NAlert>
        <article v-for="pkg in runtime.packages" :key="pkg.id" class="package-card">
          <div class="package-head"><div><strong>{{ pkg.name }}</strong><span>v{{ pkg.version }}</span><p>{{ pkg.description }}</p></div><NTag v-if="runtime.connections.some((item) => item.packageId === pkg.id)" type="success" size="small">已连接</NTag></div>
          <div v-if="!runtime.connections.some((item) => item.packageId === pkg.id)" class="package-form">
            <NFormItem label="实例名"><NInput v-model:value="instanceNames[pkg.id]" :placeholder="pkg.id" /></NFormItem>
            <NFormItem label="配置 JSON"><NInput :value="configText(pkg)" type="textarea" :autosize="{ minRows: 3, maxRows: 8 }" @update:value="packageConfig[pkg.id] = $event" /></NFormItem>
            <NButton type="primary" :loading="connecting === pkg.id" @click="connectPackage(pkg)">连接</NButton>
          </div>
          <div v-else class="connections">
            <div v-for="item in runtime.connections.filter((entry) => entry.packageId === pkg.id)" :key="item.name"><span>{{ item.name }} · {{ item.tools.length }} 个工具</span><NButton size="tiny" secondary type="error" @click="runtime.disconnectPackage(item.name)">断开</NButton></div>
          </div>
        </article>
      </NCard>

      <NCard title="底层 MCP Server">
        <NFormItem label="Server 配置 JSON"><NInput v-model:value="serverConfig" type="textarea" :autosize="{ minRows: 4, maxRows: 10 }" /></NFormItem>
        <NButton type="primary" :loading="connecting === 'server'" @click="connectServer">连接 Server</NButton>
        <div class="connections">
          <div v-for="item in runtime.serverConnections.filter((entry) => !entry.packageId)" :key="item.name"><span>{{ item.name }} · {{ item.tools.length }} 个工具</span><NButton size="tiny" secondary type="error" @click="runtime.disconnectServer(item.name)">断开</NButton></div>
        </div>
      </NCard>
    </div>
  </main>
</template>

<style scoped>
.settings-page{width:100%;height:100%;overflow:auto;padding:44px 36px 64px}.settings-header,.settings-grid{width:min(780px,100%);margin-right:auto;margin-left:auto}.settings-header{margin-bottom:21px}.settings-header p{margin:0 0 5px;color:var(--text-muted);font-size:11px;font-weight:650;letter-spacing:.06em}.settings-header h1{margin:0;font-size:27px;letter-spacing:-.03em}.settings-grid{display:grid;gap:12px}.settings-grid :deep(.n-card){border-radius:11px;background:var(--surface)}.setting-row{display:flex;align-items:center;justify-content:space-between;gap:20px}.setting-row strong,.package-card strong{font-size:13px}.setting-row p,.package-card p{margin:4px 0 0;color:var(--text-muted);font-size:12px;line-height:1.5}.runtime-note{margin:14px 0}.package-card{padding:14px 0;border-bottom:1px solid var(--border)}.package-card:last-child{border-bottom:0}.package-head{display:flex;justify-content:space-between;gap:12px}.package-head span{margin-left:8px;color:var(--text-muted);font-size:10px}.package-form{margin-top:12px;padding:12px;border-radius:8px;background:var(--surface-muted)}.package-form :deep(.n-form-item){margin-bottom:8px}.connections{display:grid;gap:7px;margin-top:10px}.connections>div{display:flex;align-items:center;justify-content:space-between;color:var(--text-muted);font-size:12px}@media(max-width:680px){.settings-page{padding:32px 16px 48px}.setting-row{align-items:flex-start;flex-direction:column}}
</style>
