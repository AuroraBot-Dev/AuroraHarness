import { expect, test } from '@playwright/test'

test('stops an active run only after the backend terminal event', async ({ page }) => {
  const session = {
    id: 'cancel-session',
    projectId: 'workspace',
    title: '停止任务',
    status: 'idle',
    runs: [] as Record<string, unknown>[],
    tasks: [],
    messages: [] as Record<string, unknown>[],
    approvals: [],
  }
  let finishCancellation!: () => void
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (socket) => {
    const send = (value: unknown) => socket.send(JSON.stringify(value))
    const event = (type: string, payload: Record<string, unknown>) =>
      send({
        protocol_version: 1,
        event_id: crypto.randomUUID(),
        occurred_at: new Date().toISOString(),
        type,
        session_id: session.id,
        run_id: 'active-run',
        payload,
      })
    let startRequestId = ''
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const reply = (result: unknown) =>
        send({ protocol_version: 1, request_id: request.request_id, ok: true, result })
      if (request.method === 'runtime.initialize')
        return reply({ protocolVersion: 1, capabilities: ['run.cancel'] })
      if (request.method === 'project.list')
        return reply({
          items: [{ id: 'workspace', name: '项目', path: '/workspace', writable: true }],
        })
      if (request.method === 'session.list') return reply({ sessions: [session] })
      if (request.method === 'session.get') return reply(session)
      if (request.method === 'run.get')
        return reply({ reviews: [], artifacts: [], agentRuns: [], events: [], tasks: [] })
      if (request.method === 'run.start') {
        startRequestId = request.request_id
        session.status = 'running'
        session.runs = [{ id: 'active-run', status: 'running', objective: request.params.goal }]
        session.messages = [
          {
            id: 'user',
            runId: 'active-run',
            role: 'user',
            content: request.params.goal,
            status: 'completed',
          },
        ]
        event('run.started', { goal: request.params.goal })
        return
      }
      if (request.method === 'run.cancel') {
        expect(request.params).toEqual({ session_id: session.id, run_id: 'active-run' })
        reply({ accepted: true })
        finishCancellation = () => {
          session.status = 'cancelled'
          session.runs[0]!.status = 'cancelled'
          session.messages.push({
            id: 'cancelled',
            runId: 'active-run',
            role: 'assistant',
            content: '用户取消了运行',
            status: 'cancelled',
          })
          event('run.cancelled', {
            sessionId: session.id,
            runId: 'active-run',
            status: 'cancelled',
          })
          event('run.resumed', {})
          event('approval.required', { interruptId: 'stale-approval' })
          send({
            protocol_version: 1,
            request_id: startRequestId,
            ok: true,
            result: {
              sessionId: session.id,
              runId: 'active-run',
              status: 'waiting',
              interruptions: [{ interruptId: 'stale-response' }],
            },
          })
        }
        return
      }
      reply({ items: [], files: [] })
    })
  })
  await page.goto('/#/sessions/cancel-session')
  const input = page.getByRole('textbox', { name: '任务内容' })
  await expect(input).toBeEnabled()
  await input.fill('执行一项长任务')
  await page.getByRole('button', { name: '发送', exact: true }).click()
  const stop = page.getByRole('button', { name: '停止运行', exact: true })
  await expect(stop).toBeEnabled()
  await stop.click()
  await expect(page.getByRole('button', { name: '正在停止', exact: true })).toBeDisabled()
  await expect(input).toBeDisabled()
  await expect.poll(() => typeof finishCancellation).toBe('function')
  finishCancellation()
  await expect(input).toBeEnabled()
  await expect(page.getByText('用户取消了运行', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '停止运行', exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '正在停止', exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
  await page.screenshot({ path: '/tmp/aurora-cancelled-run.png', animations: 'disabled' })
})
