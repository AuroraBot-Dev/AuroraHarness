import { defineStore } from 'pinia'
import type { ThemePreference } from '~/types/agent'

export const useUiStore = defineStore('ui', {
  state: () => ({ theme: 'system' as ThemePreference, sidebarCollapsed: false, hydrated: false }),
  actions: {
    hydrate() {
      if (!import.meta.client || this.hydrated) return
      const theme = localStorage.getItem('demo-agent:theme')
      if (theme === 'light' || theme === 'dark' || theme === 'system') this.theme = theme
      this.sidebarCollapsed = localStorage.getItem('demo-agent:sidebar-collapsed') === 'true'
      this.hydrated = true
    },
    setTheme(theme: ThemePreference) {
      this.theme = theme
      if (import.meta.client) localStorage.setItem('demo-agent:theme', theme)
    },
    toggleSidebar() {
      this.sidebarCollapsed = !this.sidebarCollapsed
      if (import.meta.client) localStorage.setItem('demo-agent:sidebar-collapsed', String(this.sidebarCollapsed))
    },
  },
})
