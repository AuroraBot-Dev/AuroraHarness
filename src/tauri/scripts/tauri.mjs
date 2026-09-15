#!/usr/bin/env node
// Tauri CLI 只会在当前目录（以及最多三层子目录）里寻找 tauri.conf.json，而本仓库把
// Rust 壳放在 src/tauri、把前端放在 src/frontend——两者互不为子目录，因此 CLI 无法
// 自行定位。这里显式传入 TAURI_APP_PATH / TAURI_FRONTEND_PATH，让
// `pnpm tauri dev|build` 从 src/frontend 正常驱动。
//
// 必须在 src/frontend 下通过 pnpm 调用，这样 node_modules/.bin 才会在 PATH 里。

import { spawn } from 'node:child_process'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const tauriDir = fileURLToPath(new URL('..', import.meta.url))
const frontendDir = fileURLToPath(new URL('../../frontend/', import.meta.url))
const argv = process.argv.slice(2)

// Windows 上的 pnpm shim 是 .cmd，Node 无法直接 spawn（会把参数交给 cmd 解析），
// 因此显式经由 ComSpec。这比 shell:true 更明确，也不会触发 Node 的弃用警告。
const isWindows = process.platform === 'win32'
const command = isWindows ? (process.env.ComSpec ?? 'cmd.exe') : 'tauri'
const args = isWindows ? ['/d', '/c', 'tauri', ...argv] : argv

const child = spawn(command, args, {
  stdio: 'inherit',
  env: {
    ...process.env,
    TAURI_APP_PATH: tauriDir,
    TAURI_FRONTEND_PATH: frontendDir,
  },
})

child.on('error', (error) => {
  console.error(`[aurora] 无法启动 Tauri CLI: ${error.message}`)
  console.error('[aurora] 请在 src/frontend 下运行 pnpm tauri。')
  process.exit(1)
})

child.on('exit', (code, signal) => {
  process.exit(signal ? 1 : (code ?? 0))
})
