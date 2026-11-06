<script setup lang="ts">
defineProps<{ title: string; description?: string }>()
</script>

<template>
  <header class="page-heading">
    <div class="page-heading-copy">
      <h1>{{ title }}</h1>
      <p
        v-if="description"
        class="page-description"
      >
        {{ description }}
      </p>
    </div>
    <div
      v-if="$slots.default"
      class="page-heading-actions"
    >
      <slot />
    </div>
  </header>
</template>
