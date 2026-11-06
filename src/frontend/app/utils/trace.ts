import type { ToolCallTrace } from '~/types/trace'

export const traceStatus: Record<string, string> = {
  queued: '排队中',
  running: '执行中',
  waiting: '等待输入',
  completed: '已完成',
  failed: '失败',
  cancelled: '已取消',
  interrupted: '已中断',
  skipped: '已跳过',
}

export const stageLabel: Record<string, string> = {
  code: '编写',
  capture: '预览',
  review: '审查',
  summary: '汇总',
}

export function normalizeToolCall(raw: Record<string, unknown>): ToolCallTrace {
  return {
    id: String(raw.id ?? raw.tool_call_id ?? ''),
    runId: String(raw.runId ?? raw.run_id ?? ''),
    agentRunId: String(raw.agentRunId ?? raw.agent_run_id ?? ''),
    taskId: String(raw.taskId ?? raw.task_id ?? '') || null,
    taskKey: String(raw.taskKey ?? raw.task_key ?? ''),
    toolName: String(raw.toolName ?? raw.tool_name ?? ''),
    input: raw.input ?? raw.inputJson ?? raw.input_json ?? {},
    output: String(raw.output ?? ''),
    error: String(raw.error ?? ''),
    status: String(raw.status ?? 'running'),
    startedAt: String(raw.startedAt ?? raw.started_at ?? ''),
    endedAt: String(raw.endedAt ?? raw.ended_at ?? '') || null,
    durationMs:
      typeof (raw.durationMs ?? raw.duration_ms) === 'number'
        ? Number(raw.durationMs ?? raw.duration_ms)
        : null,
  }
}

export function formatTraceDuration(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms)) return '—'
  if (ms < 1) return '<1 ms'
  if (ms < 1000) return `${Math.round(ms)} ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`
  return `${Math.floor(ms / 60_000)} m ${Math.floor((ms % 60_000) / 1000)} s`
}

export function traceTime(value: string): string {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString('zh-CN', { hour12: false })
}

export function traceInput(value: unknown): string {
  if (typeof value === 'string') {
    try {
      return JSON.stringify(JSON.parse(value), null, 2)
    } catch {
      return value
    }
  }
  return JSON.stringify(value, null, 2) ?? ''
}

export function tracePreview(value: unknown): string {
  if (!value || typeof value !== 'object') return String(value ?? '')
  const args = value as Record<string, unknown>
  return String(
    args.path ??
      args.command ??
      args.code ??
      args.query ??
      args.goal ??
      Object.keys(args).join(' · '),
  )
    .replace(/\s+/g, ' ')
    .slice(0, 160)
}
