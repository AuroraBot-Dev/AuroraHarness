import { expect, test, type WebSocketRoute } from '@playwright/test'

test('reparents a late recursive tree and preserves a completed leaf across replayed events', async ({ page }) => {
  let socket: WebSocketRoute
  const session = { id: 'tree', title: '递归任务', projectId: 'project', status: 'running', runs: [{ id: 'run', status: 'running', objective: '递归任务' }], tasks: [], messages: [] }
  await page.routeWebSocket(/ws:\/\/127\.0\.0\.1:.*\/ws/, (connection) => {
    socket = connection
    socket.onMessage((raw) => {
      const request = JSON.parse(String(raw))
      const result = request.method === 'runtime.initialize' ? { protocolVersion: 1 }
        : request.method === 'project.list' ? { items: [{ id: 'project', name: '项目', path: '/workspace', writable: true }] }
          : request.method === 'session.list' ? { sessions: [session] }
            : request.method === 'session.get' ? session
              : request.method === 'run.get' ? { agentRuns: [], reviews: [], artifacts: [] }
                : { items: [], files: [] }
      socket.send(JSON.stringify({ protocol_version: 1, request_id: request.request_id, ok: true, result }))
    })
  })
  const send = (type: string, task: Record<string, unknown>) => socket.send(JSON.stringify({ protocol_version: 1, event_id: crypto.randomUUID(), occurred_at: new Date().toISOString(), type, session_id: 'tree', run_id: 'run', task_id: task.id, payload: { task } }))
  await page.goto('/#/sessions/tree')
  await expect(page.getByRole('heading', { name: '递归任务', exact: true })).toBeVisible()
  send('task.started', { id: 'leaf' })
  send('task.created', { id: 'leaf', parent_id: 'b', description: '最深层写入', tool: 'write_file', status: 'queued' })
  const tree = page.locator('.task-tree-panel')
  await expect(tree.getByRole('button', { name: /最深层写入/ })).toBeVisible()
  send('task.created', { id: 'b', parent_id: 'a', description: '第二层委派', tool: 'call_agent' })
  send('task.created', { id: 'a', parent_id: 'root', description: '第一层委派', tool: 'call_agent' })
  send('task.created', { id: 'root', parent_id: null, description: '根委派', tool: 'call_agent' })
  const root = tree.locator('[data-task-id="root"]')
  await expect(root.locator('[data-task-id="a"] [data-task-id="b"] [data-task-id="leaf"]')).toBeVisible()
  send('task.completed', { id: 'leaf', output: '叶子已完成', status: 'completed' })
  send('task.started', { id: 'leaf' })
  await expect(tree.locator('[data-task-id="leaf"] .task-status')).toHaveClass(/completed/)
  await tree.getByRole('button', { name: /最深层写入/ }).click()
  await expect(page.getByText('叶子已完成', { exact: true })).toBeVisible()
})
