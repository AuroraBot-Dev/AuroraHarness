<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NMessageProvider,
  darkTheme,
  type GlobalThemeOverrides,
} from 'naive-ui'

const isDark = ref(false)
const ui = useUiStore()
const runtime = useRuntimeStore()
let mediaQuery: MediaQueryList | null = null

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#2563eb',
    primaryColorHover: '#1d4ed8',
    primaryColorPressed: '#1e40af',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
    fontFamily: 'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
}

function syncTheme(event?: MediaQueryListEvent) {
  const systemDark = event?.matches ?? mediaQuery?.matches ?? false
  isDark.value = ui.theme === 'dark' || (ui.theme === 'system' && systemDark)
  document.documentElement.dataset.theme = isDark.value ? 'dark' : 'light'
}

watch(() => ui.theme, () => syncTheme())

onMounted(() => {
  ui.hydrate()
  mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  syncTheme()
  mediaQuery.addEventListener('change', syncTheme)
  runtime.initialize().catch(() => {})
})

onBeforeUnmount(() => mediaQuery?.removeEventListener('change', syncTheme))

useHead({ titleTemplate: (title) => title ? `${title} · Aurora Agent` : 'Aurora Agent' })
</script>

<template>
  <NConfigProvider :theme="isDark ? darkTheme : null" :theme-overrides="themeOverrides">
    <NGlobalStyle />
    <NDialogProvider>
      <NMessageProvider>
        <AppShell>
          <NuxtPage />
        </AppShell>
      </NMessageProvider>
    </NDialogProvider>
  </NConfigProvider>
</template>
