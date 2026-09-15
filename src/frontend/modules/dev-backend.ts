import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

interface BackendProcessState {
  child: ChildProcess | null
  exitHookRegistered: boolean
}

const processState = globalThis as typeof globalThis & {
  __auroraBackendProcess?: BackendProcessState
}
const backendState = processState.__auroraBackendProcess ??= {
  child: null,
  exitHookRegistered: false,
}

interface NuxtLike {
  options: { dev: boolean }
  hook: (name: string, callback: () => void) => void
}

export default function devBackend(_inlineOptions: unknown, nuxt: NuxtLike) {
  if (!nuxt.options.dev) return
  if (process.env.TAURI_ENV_PLATFORM) return
  if (process.env.VITE_RUNTIME_WS) return

  // 运行时由 src/cli 与 src/agent 两个分布提供，`uv run` 必须在 uv 工作区根执行。
  // 本文件位于 <repo>/src/frontend/modules，往上三级即仓库根。
  const repoRoot = process.env.AURORA_ROOT
    ?? fileURLToPath(new URL('../../../', import.meta.url))

  const stopBackend = () => {
    const running = backendState.child
    backendState.child = null
    if (!running || running.killed) return
    if (process.platform !== 'win32' && running.pid) {
      try {
        process.kill(-running.pid, 'SIGTERM')
        return
      }
      catch {
        running.kill('SIGTERM')
        return
      }
    }
    running.kill('SIGTERM')
  }

  nuxt.hook('listen', () => {
    if (backendState.child) return
    if (!existsSync(repoRoot)) {
      console.error(`[aurora] 仓库根目录不存在: ${repoRoot}。请设置 AURORA_ROOT 指向 AuroraHarness 仓库根。`)
      return
    }
    const child = spawn('uv', ['run', '--no-sync', 'aurora', 'runtime', '--port', '8765'], {
      cwd: repoRoot,
      stdio: 'inherit',
      env: process.env,
      detached: process.platform !== 'win32',
    })
    backendState.child = child
    child.on('error', (error) => {
      console.error(`[aurora] 后端启动失败: ${error.message}`)
      if (backendState.child === child) backendState.child = null
    })
    child.on('exit', () => {
      if (backendState.child === child) backendState.child = null
    })
  })

  nuxt.hook('close', stopBackend)
  if (!backendState.exitHookRegistered) {
    process.once('exit', stopBackend)
    backendState.exitHookRegistered = true
  }
}
