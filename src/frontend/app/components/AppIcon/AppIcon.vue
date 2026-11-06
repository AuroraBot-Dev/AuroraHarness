<script setup lang="ts">
import type { Component } from 'vue'

type IconWeight = 'thin' | 'light' | 'regular' | 'bold' | 'fill' | 'duotone'

withDefaults(
  defineProps<{
    component: Component
    size?: number | string
    weight?: IconWeight
  }>(),
  { size: '1em', weight: 'light' },
)
</script>

<template>
  <component
    :is="component"
    class="app-icon"
    :size="size"
    :weight="weight"
    aria-hidden="true"
  />
</template>

<style scoped>
.app-icon {
  display: inline-block;
  flex: 0 0 auto;
  vertical-align: middle;
}
</style>
