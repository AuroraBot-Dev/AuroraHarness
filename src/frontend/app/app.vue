<script setup lang="ts">
import {
  NConfigProvider,
  NDialogProvider,
  NGlobalStyle,
  NMessageProvider,
  darkTheme,
  type GlobalThemeOverrides,
} from 'naive-ui'
import { accentPalette } from '~/utils/appearance'

const isDark = ref(false)
const ui = useUiStore()
const runtime = useRuntimeStore()
let mediaQuery: MediaQueryList | null = null
const palette = computed(() => accentPalette(ui.accent, isDark.value))

const themeOverrides = computed<GlobalThemeOverrides>(() => ({
  common: {
    primaryColor: palette.value.primary,
    primaryColorHover: palette.value.hover,
    primaryColorPressed: palette.value.pressed,
    borderRadius: '10px',
    borderRadiusSmall: '7px',
    fontFamily:
      '"PingFang SC", -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif',
  },
  Button: {
    textColorPrimary: palette.value.foreground,
    textColorHoverPrimary: palette.value.foreground,
    textColorPressedPrimary: palette.value.foreground,
    textColorFocusPrimary: palette.value.foreground,
  },
}))

function syncTheme(event?: MediaQueryListEvent) {
  const systemDark = event?.matches ?? mediaQuery?.matches ?? false
  isDark.value = ui.theme === 'dark' || (ui.theme === 'system' && systemDark)
  document.documentElement.dataset.theme = isDark.value ? 'dark' : 'light'
  document.documentElement.dataset.material = ui.material
  document.documentElement.style.setProperty('--accent-source', ui.accent)
  document.documentElement.style.setProperty('--accent', palette.value.primary)
  document.documentElement.style.setProperty('--accent-soft', palette.value.soft)
}

watch(
  () => [ui.theme, ui.accent, ui.material],
  () => syncTheme(),
)

onMounted(() => {
  ui.hydrate()
  mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
  syncTheme()
  mediaQuery.addEventListener('change', syncTheme)
  runtime.initialize().catch(() => {})
})

onBeforeUnmount(() => mediaQuery?.removeEventListener('change', syncTheme))

useHead({ titleTemplate: (title) => (title ? `${title} · Aurora Agent` : 'Aurora Agent') })
</script>

<template>
  <NConfigProvider
    :theme="isDark ? darkTheme : null"
    :theme-overrides="themeOverrides"
  >
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
