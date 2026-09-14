import { defineStore } from 'pinia'
import type { SessionRecord } from '~/types/agent'
import { normalizeSession, normalizeMessage } from '~/utils/normalizers'
import { runtimeRequest } from '~/utils/runtimeClient'
import { useProjectStore } from '~/stores/projectStore'

interface SessionCreateResult { sessionId: string; projectId: string; workflowId: string }

export const useSessionStore = defineStore('sessions', {
  state: () => ({ sessions: [] as SessionRecord[], activeSessionId: null as string | null, loaded: true, loading: false }),
  getters: {
    activeSession: (state) => state.sessions.find((item) => item.id === state.activeSessionId),
    sorted(state): SessionRecord[] { return [...state.sessions].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)) },
    isRunning: (state) => (sessionId: string) => state.sessions.some((item) => item.id === sessionId && ['running', 'waiting', 'queued'].includes(item.status)),
  },
  actions: {
    getSession(id: string) { return this.sessions.find((item) => item.id === id) },
    upsert(raw: unknown) {
      const session = normalizeSession(raw as Record<string, unknown>)
      const index = this.sessions.findIndex((item) => item.id === session.id)
      if (index >= 0) this.sessions[index] = session
      else this.sessions.push(session)
      return session
    },
    setActive(id: string | null) { this.activeSessionId = id },
    remove(id: string) { this.sessions = this.sessions.filter((item) => item.id !== id); if (this.activeSessionId === id) this.activeSessionId = null },
    removeByProject(projectId: string) { this.sessions = this.sessions.filter((item) => item.projectId !== projectId) },
    async loadAll() {
      this.loading = true
      try {
        const result = await runtimeRequest<{ sessions: Record<string, unknown>[] }>('session.list')
        const existing = new Map(this.sessions.map((item) => [item.id, item]))
        this.sessions = result.sessions.map((raw) => {
          const summary = normalizeSession(raw)
          const old = existing.get(summary.id)
          return old ? { ...old, title: summary.title, status: summary.status, updatedAt: summary.updatedAt } : summary
        })
        this.loaded = true
      } finally { this.loading = false }
    },
    async load(id: string) {
      const raw = await runtimeRequest<Record<string, unknown>>('session.get', { sessionId: id })
      return this.upsert(raw)
    },
    async loadOlder(id: string) {
      const session = this.getSession(id)
      if (!session?.nextBeforeSeq) return
      const result = await runtimeRequest<{ messages: Record<string, unknown>[]; nextBeforeSeq: number | null }>('message.list', { sessionId: id, beforeSeq: session.nextBeforeSeq })
      const known = new Set(session.messages.map((item) => item.id))
      session.messages.unshift(...result.messages.map((item) => normalizeMessage(item, id)).filter((item) => !known.has(item.id)))
      session.nextBeforeSeq = result.nextBeforeSeq
    },
    async create(title = '新对话', projectId?: string | null) {
      const projects = useProjectStore()
      const targetId = projectId || projects.activeProjectId || projects.projects[0]?.id
      const project = targetId ? projects.byId(targetId) : undefined
      if (!project) throw new Error('请先添加一个工作区')
      const result = await runtimeRequest<SessionCreateResult>('session.create', {
        workspacePath: project.path, sandboxMode: 'workspace-write', approvalMode: 'interactive', title,
      })
      const now = new Date().toISOString()
      const session: SessionRecord = {
        id: String(result.sessionId), title, projectId: result.projectId, workflowId: result.workflowId, createdAt: now, updatedAt: now,
        status: 'idle', messages: [], runs: [], tasks: [], approvals: [],
      }
      this.sessions.push(session); this.activeSessionId = session.id
      return session
    },
    async rename(id: string, title: string) {
      const session = this.getSession(id)
      if (!session) throw new Error('会话不存在')
      await runtimeRequest('session.update', { sessionId: id, title })
      session.title = title; session.updatedAt = new Date().toISOString()
      return session
    },
    async delete(id: string) { await runtimeRequest('session.delete', { sessionId: id }); this.remove(id) },
    async clearAll() {
      await Promise.all(this.sessions.map((item) => runtimeRequest('session.delete', { sessionId: item.id })))
      this.sessions = []; this.activeSessionId = null
    },
  },
})
