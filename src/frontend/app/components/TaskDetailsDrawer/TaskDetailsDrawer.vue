<script setup lang="ts">
import { NDescriptions, NDescriptionsItem, NDrawer, NDrawerContent, NTag } from 'naive-ui'
import type { TaskNode } from '~/types/agent'

const model = defineModel<TaskNode | null>('show', { required: true })
</script>

<template>
  <NDrawer :show="!!model" :width="420" placement="right" @update:show="(show) => { if (!show) model = null }">
    <NDrawerContent v-if="model" title="任务详情" closable>
      <NDescriptions :column="1" label-placement="top" size="small">
        <NDescriptionsItem label="任务">{{ model.description }}</NDescriptionsItem>
        <NDescriptionsItem label="状态"><NTag size="small" :bordered="false">{{ model.status }}</NTag></NDescriptionsItem>
        <NDescriptionsItem label="推理强度">{{ model.effort }}</NDescriptionsItem>
        <NDescriptionsItem label="输出"><pre>{{ model.output || '暂无输出' }}</pre></NDescriptionsItem>
        <NDescriptionsItem v-if="model.error" label="错误"><span class="error">{{ model.error }}</span></NDescriptionsItem>
      </NDescriptions>
    </NDrawerContent>
  </NDrawer>
</template>

<style scoped>pre{margin:0;white-space:pre-wrap;font:12px/1.65 inherit}.error{color:#c33f3f}</style>
