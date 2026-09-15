#!/usr/bin/env node
// AuroraHarness 的统一任务入口。
//
// 全部用 Node 实现，不依赖 make，也不依赖 bash：开发者在 Windows 与 macOS 上敲的是同一套命令，
// 而 Node 本来就是前端层的硬依赖。上一版用 Makefile 分发任务，Windows 上没装 make 就等于没有
// 任何入口，而且 recipe 里的 rm -rf / bash 也是 Unix 专有写法。
//
//   node scripts/tasks.mjs <任务> [-- 透传参数]
//
// 参数透传示例：node scripts/tasks.mjs build-desktop -- --bundles nsis

import { spawnSync } from 'node:child_process'
import { copyFileSync, existsSync, rmSync } from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const REPO_ROOT = fileURLToPath(new URL('..', import.meta.url))
const FRONTEND = path.join(REPO_ROOT, 'src', 'frontend')
const TAURI = path.join(REPO_ROOT, 'src', 'tauri')
const IS_WINDOWS = process.platform === 'win32'
const COMSPEC = process.env.ComSpec ?? 'cmd.exe'

// 本项目必须用 pnpm 11：patches/ 下的补丁文件在 pnpm 12 上会被更严格的解析器拒绝
// （ERR_PNPM_INVALID_PATCH），报错信息完全指不到原因。
const REQUIRED_PNPM_MAJOR = 11

function fail(message) {
  console.error(`\n错误: ${message}`)
  process.exit(1)
}

/**
 * 执行一步命令并等待结束。
 *
 * Windows 上的 pnpm / npx / 本地 .bin 入口都是 .cmd 垫片，Node 无法直接执行，必须经由命令
 * 解释器；uv、cargo、node 这些真实 .exe 直接 exec 即可（也不必担心多一层引号解析）。
 */
function step(command, args, { capture = false, cwd = REPO_ROOT } = {}) {
  const shellRequired =
    IS_WINDOWS && (command.endsWith('.cmd') || ['pnpm', 'npx'].includes(command))
  const executable = shellRequired ? COMSPEC : command
  const argv = shellRequired ? ['/d', '/c', command, ...args] : args
  console.log(`\n$ ${[command, ...args].join(' ')}`)
  const result = spawnSync(executable, argv, {
    cwd,
    stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
    encoding: 'utf8',
  })
  if (result.error) fail(`无法执行 ${command}: ${result.error.message}`)
  if (result.status !== 0) fail(`${command} ${args.join(' ')} 退出码 ${result.status}`)
  return (result.stdout ?? '').trim()
}

/** 返回可用的 pnpm 调用方式，版本不符时退回到 npx pnpm@11。 */
function pnpm() {
  const raw = step('pnpm', ['--version'], { capture: true })
  const major = Number.parseInt(raw.split('.')[0], 10)
  if (major === REQUIRED_PNPM_MAJOR) return { command: 'pnpm', args: [] }
  console.warn(
    `\n提示: 检测到 pnpm ${raw}，本项目需要 ${REQUIRED_PNPM_MAJOR}.x。` +
      `改用 npx pnpm@${REQUIRED_PNPM_MAJOR}（用 corepack 固定版本可去掉这次下载）。`,
  )
  return { command: 'npx', args: ['--yes', `pnpm@${REQUIRED_PNPM_MAJOR}`] }
}

/** 在 src/frontend 下执行 pnpm 脚本。 */
function pnpmRun(script, extraArgs = []) {
  const { command, args } = pnpm()
  step(command, [...args, 'run', script, ...extraArgs], { cwd: FRONTEND })
}

function missingPythonDistributions() {
  return ['agent', 'cli'].filter(
    (name) => !existsSync(path.join(REPO_ROOT, 'src', name, 'pyproject.toml')),
  )
}

function requirePythonDistributions() {
  const missing = missingPythonDistributions()
  if (missing.length > 0) fail(`缺少 Python 分布: ${missing.map((n) => `src/${n}`).join(', ')}`)
}

function buildSidecar() {
  requirePythonDistributions()
  step(process.execPath, [path.join(TAURI, 'scripts', 'build-sidecar.mjs')])
}

const tasks = {
  setup() {
    requirePythonDistributions()
    const envExample = path.join(REPO_ROOT, 'src', 'agent', '.env.example')
    const env = path.join(REPO_ROOT, 'src', 'agent', '.env')
    if (!existsSync(env) && existsSync(envExample)) {
      copyFileSync(envExample, env)
      console.log(`已创建 ${path.relative(REPO_ROOT, env)}，请填入模型配置。`)
    }
    step('uv', ['sync', '--frozen'])

    const { command, args } = pnpm()
    step(command, [...args, 'install', '--frozen-lockfile'], { cwd: FRONTEND })

    // Git 钩子必须从仓库根安装：唯一有效的配置是根目录的 lefthook.yml。若在子目录里执行，
    // lefthook 找不到配置就会就地生成一份游离的默认模板，两处并存后无人知道用了哪份。
    const lefthook = path.join(
      FRONTEND,
      'node_modules',
      '.bin',
      IS_WINDOWS ? 'lefthook.cmd' : 'lefthook',
    )
    if (existsSync(lefthook)) step(lefthook, ['install'])
    else console.warn('提示: 未找到 lefthook，跳过 Git 钩子安装。')

    console.log('\nSetup 完成。')
  },

  'dev-runtime': () => step('uv', ['run', 'aurora', 'runtime', '--port', '8765']),
  'dev-web': () => pnpmRun('dev'),
  'dev-desktop': () => pnpmRun('tauri', ['dev']),

  lint() {
    step('uv', ['run', '--frozen', 'ruff', 'check', '.'])
    step('uv', ['run', '--frozen', 'ruff', 'format', '--check', '.'])
    step('uv', ['run', '--frozen', 'pyright'])
    pnpmRun('lint')
  },

  fmt() {
    step('uv', ['run', '--frozen', 'ruff', 'format', '.'])
    step('uv', ['run', '--frozen', 'ruff', 'check', '--fix', '.'])
    pnpmRun('lint:fix')
  },

  'test-python': () => step('uv', ['run', '--frozen', 'pytest']),
  'test-rust': () => step('cargo', ['test', '--workspace']),

  'test-web'() {
    pnpmRun('typecheck')
    pnpmRun('test:coverage')
  },

  test() {
    tasks.lint()
    tasks['test-python']()
    tasks['test-rust']()
    tasks['test-web']()
  },

  'build-sidecar': buildSidecar,

  /** 构建桌面安装包。Windows 上默认只出 NSIS 的 .exe，避开 MSI 需要的 WiX 工具链。 */
  'build-desktop'(extra) {
    buildSidecar()
    const bundles = extra.length > 0 ? extra : IS_WINDOWS ? ['--bundles', 'nsis'] : []
    pnpmRun('tauri', ['build', ...bundles])
  },

  clean() {
    step('cargo', ['clean'])
    for (const target of [
      path.join(TAURI, 'resources', 'sidecar', 'python'),
      path.join(TAURI, 'resources', 'sidecar', 'site-packages'),
      path.join(FRONTEND, '.output'),
      path.join(FRONTEND, '.nuxt'),
    ]) {
      rmSync(target, { recursive: true, force: true })
      console.log(`已清理 ${path.relative(REPO_ROOT, target)}`)
    }
  },
}

function usage() {
  let self = path.relative(REPO_ROOT, fileURLToPath(import.meta.url)).replace(/\\/g, '/')
  console.log(`用法: node ${self} <任务> [-- 透传参数]\n`)
  console.log('可用任务:')
  for (const name of Object.keys(tasks)) console.log(`  ${name}`)
}

const [, , name, ...rest] = process.argv
if (!name || name === 'help' || name === '--help') {
  usage()
  process.exit(0)
}
if (!Object.hasOwn(tasks, name)) {
  console.error(`未知任务: ${name}`)
  usage()
  process.exit(1)
}

const separator = rest.indexOf('--')
tasks[name](separator === -1 ? [] : rest.slice(separator + 1))
