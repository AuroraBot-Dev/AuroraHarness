<script setup lang="ts">
import { NTooltip } from 'naive-ui'

const runtime = useRuntimeStore()
const label = computed(() => ({
  connected: '运行时已连接',
  connecting: '正在连接运行时',
  disconnected: '运行时未连接',
  restarting: '正在重启运行时',
}[runtime.connectionStatus]))
</script>

<template>
  <NTooltip placement="right">
    <template #trigger>
      <span class="runtime-status" :class="runtime.connectionStatus" aria-label="运行时连接状态">
        <span class="status-dot" />
        <span class="status-label">{{ label }}</span>
      </span>
    </template>
    {{ label }}
  </NTooltip>
</template>

<style scoped>
.runtime-status { display: inline-flex; align-items: center; gap: 7px; color: var(--text-muted); font-size: 12px; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #9ca3af; }
.connected .status-dot { background: #22a06b; }
.connecting .status-dot { background: #d69e2e; }
.restarting .status-dot { background: #d69e2e; }
.disconnected .status-dot { background: #d14343; }
@media (max-width: 860px) { .status-label { display: none; } }
</style>
