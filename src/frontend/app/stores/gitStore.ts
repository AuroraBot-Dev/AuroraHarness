import { defineStore } from 'pinia'
import type { GitDiff, GitStatus, GitView } from '~/types/agent'
import { runtimeRequest } from '~/utils/runtimeClient'

const keyFor = (sessionId: string, view: GitView, runId?: string | null) => `${sessionId}:${view}:${runId ?? ''}`

export const useGitStore = defineStore('git', {
  state: () => ({
    statuses: {} as Record<string, GitStatus>,
    diffs: {} as Record<string, GitDiff>,
    loading: false,
    diffLoading: false,
    rollingBack: false,
    error: '',
    statusRequestVersions: {} as Record<string, number>,
    diffRequestVersions: {} as Record<string, number>,
  }),
  actions: {
    statusFor(sessionId: string, view: GitView, runId?: string | null) {
      return this.statuses[keyFor(sessionId, view, runId)]
    },
    diffFor(sessionId: string, view: GitView, path: string, runId?: string | null) {
      return this.diffs[`${keyFor(sessionId, view, runId)}:${path}`]
    },
    async loadStatus(sessionId: string, view: GitView, runId?: string | null) {
      const key = keyFor(sessionId, view, runId)
      const version = (this.statusRequestVersions[key] ?? 0) + 1
      this.statusRequestVersions[key] = version
      this.loading = true
      this.error = ''
      try {
        const status = await runtimeRequest<GitStatus>('git.status', { sessionId, view, ...(runId ? { runId } : {}) })
        if (version === this.statusRequestVersions[key]) this.statuses[key] = status
        return status
      } catch (error) {
        if (version === this.statusRequestVersions[key]) this.error = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        if (version === this.statusRequestVersions[key]) this.loading = false
      }
    },
    async loadDiff(sessionId: string, view: GitView, path: string, runId?: string | null) {
      const key = `${keyFor(sessionId, view, runId)}:${path}`
      const version = (this.diffRequestVersions[key] ?? 0) + 1
      this.diffRequestVersions[key] = version
      this.diffLoading = true
      this.error = ''
      try {
        const diff = await runtimeRequest<GitDiff>('git.diff', { sessionId, view, path, ...(runId ? { runId } : {}) })
        if (version === this.diffRequestVersions[key]) this.diffs[key] = diff
        return diff
      } catch (error) {
        if (version === this.diffRequestVersions[key]) this.error = error instanceof Error ? error.message : String(error)
        throw error
      } finally {
        if (version === this.diffRequestVersions[key]) this.diffLoading = false
      }
    },
    async rollback(sessionId: string, runId: string) {
      this.rollingBack = true
      this.error = ''
      try {
        await runtimeRequest('git.rollback', { sessionId, runId })
        this.clearSession(sessionId)
      } catch (error) {
        this.error = error instanceof Error ? error.message : String(error)
        throw error
      } finally { this.rollingBack = false }
    },
    clearSession(sessionId: string) {
      this.statuses = Object.fromEntries(
        Object.entries(this.statuses).filter(([key]) => !key.startsWith(`${sessionId}:`)),
      )
      this.diffs = Object.fromEntries(
        Object.entries(this.diffs).filter(([key]) => !key.startsWith(`${sessionId}:`)),
      )
      this.statusRequestVersions = Object.fromEntries(
        Object.entries(this.statusRequestVersions).filter(([key]) => !key.startsWith(`${sessionId}:`)),
      )
      this.diffRequestVersions = Object.fromEntries(
        Object.entries(this.diffRequestVersions).filter(([key]) => !key.startsWith(`${sessionId}:`)),
      )
    },
  },
})
