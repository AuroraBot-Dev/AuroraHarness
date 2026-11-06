import { defineStore } from 'pinia'
import type { ThemePreference } from '~/types/agent'
import { isAccentColor } from '~/utils/appearance'

export const useUiStore = defineStore('ui', {
  state: () => ({
    theme: 'system' as ThemePreference,
    accent: '#d53543',
    material: 'glass' as 'glass' | 'solid',
    sidebarCollapsed: false,
    hydrated: false,
  }),
  actions: {
    hydrate() {
      if (!import.meta.client || this.hydrated) return
      const theme = localStorage.getItem('demo-agent:theme')
      if (theme === 'light' || theme === 'dark' || theme === 'system') this.theme = theme
      const accent = localStorage.getItem('aurora:accent:v1')
      if (accent && isAccentColor(accent)) this.accent = accent
      const material = localStorage.getItem('aurora:material:v1')
      if (material === 'glass' || material === 'solid') this.material = material
      this.sidebarCollapsed = localStorage.getItem('demo-agent:sidebar-collapsed') === 'true'
      this.hydrated = true
    },
    setTheme(theme: ThemePreference) {
      this.theme = theme
      if (import.meta.client) localStorage.setItem('demo-agent:theme', theme)
    },
    setAccent(color: string) {
      if (!isAccentColor(color)) return
      this.accent = color.toLowerCase()
      if (import.meta.client) localStorage.setItem('aurora:accent:v1', this.accent)
    },
    setMaterial(material: 'glass' | 'solid') {
      this.material = material
      if (import.meta.client) localStorage.setItem('aurora:material:v1', material)
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      if (import.meta.client)
        localStorage.setItem('demo-agent:sidebar-collapsed', String(this.sidebarCollapsed))
    },
  },
})
