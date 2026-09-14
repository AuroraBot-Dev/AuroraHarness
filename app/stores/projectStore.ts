import { defineStore } from 'pinia'
import type { ProjectRecord } from '~/types/agent'
import { normalizeProject } from '~/utils/normalizers'
import { runtimeRequest } from '~/utils/runtimeClient'
import { useSessionStore } from '~/stores/sessionStore'

const storageKey = 'aurora:workspaces:v1'
interface WorkspaceResult { name: string; path: string; isGitRepository: boolean; writable: boolean }

export const useProjectStore = defineStore('projects', {
  state: () => ({
    projects: [] as ProjectRecord[], activeProjectId: null as string | null,
    loaded: false, loading: false,
  }),
  getters: {
    activeProject: (state) => state.projects.find((item) => item.id === state.activeProjectId),
    byId: (state) => (id: string) => state.projects.find((item) => item.id === id),
  },
  actions: {
    setActive(id: string | null) { this.activeProjectId = id; this.persist() },
    persist() {
      if (import.meta.client) localStorage.setItem(storageKey, JSON.stringify({ projects: this.projects, activeProjectId: this.activeProjectId }))
    },
    async loadAll() {
      if (this.loaded || !import.meta.client) return
      this.loading = true
      try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || '{}')
        if (Array.isArray(saved.projects) && saved.projects.length) {
          await runtimeRequest('project.import', { sourceKey: storageKey, projects: saved.projects })
        }
        const result = await runtimeRequest<{ items: Record<string, unknown>[] }>('project.list')
        this.projects = result.items.map(normalizeProject)
        this.activeProjectId = typeof saved.activeProjectId === 'string' ? saved.activeProjectId : this.projects[0]?.id ?? null
        this.loaded = true
      } finally { this.loading = false }
    },
    async get(id: string) {
      const project = this.byId(id)
      if (!project) throw new Error('工作区不存在')
      let workspace = await runtimeRequest<WorkspaceResult>('workspace.validate', { path: project.path })
      if (!workspace.isGitRepository) workspace = await runtimeRequest<WorkspaceResult>('workspace.git.initialize', { path: project.path })
      project.isGitRepository = workspace.isGitRepository
      project.writable = workspace.writable
      this.persist()
      return project
    },
    async create(path: string) {
      let workspace = await runtimeRequest<WorkspaceResult>('workspace.validate', { path })
      if (!workspace.isGitRepository) workspace = await runtimeRequest<WorkspaceResult>('workspace.git.initialize', { path: workspace.path })
      const existing = this.projects.find((item) => item.path === workspace.path)
      if (existing) { this.activeProjectId = existing.id; this.persist(); return existing }
      const raw = await runtimeRequest<Record<string, unknown>>('project.create', { data: { name: workspace.name, path: workspace.path } })
      const project = normalizeProject({ ...raw, ...workspace })
      this.projects.push(project)
      this.activeProjectId = project.id
      this.persist()
      return project
    },
    async rename(id: string, name: string) {
      const project = this.byId(id)
      if (!project) throw new Error('工作区不存在')
      await runtimeRequest('project.update', { id, data: { name } })
      project.name = name; project.updatedAt = new Date().toISOString(); this.persist()
      return project
    },
    async delete(id: string) {
      const sessionStore = useSessionStore()
      await Promise.all(sessionStore.sessions.filter((item) => item.projectId === id).map((item) => sessionStore.delete(item.id)))
      await runtimeRequest('project.delete', { id })
      this.projects = this.projects.filter((item) => item.id !== id)
      if (this.activeProjectId === id) this.activeProjectId = this.projects[0]?.id ?? null
      this.persist()
    },
    remove(id: string) { this.projects = this.projects.filter((item) => item.id !== id); this.persist() },
  },
})
