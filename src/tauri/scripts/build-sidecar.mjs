#!/usr/bin/env node
// 把 Python 运行时打包进 Tauri 资源目录，供发行版使用。
//
// 属于 tauri 层：它决定「打包后的桌面应用怎么启动 runtime」。用 Node 而不是 bash，是为了让
// Windows 与 macOS 跑同一条命令、同一份逻辑——原先的 bash 版只按 Unix 布局写，在 Windows 上
// 先后踩过 venv 布局（解释器在 Scripts/ 而非 bin/）与 .pth（依赖必须落在解释器自身的
// site-packages，否则 pywin32 的 DLL bootstrap 不会执行）两个坑。
//
// 产出约定，必须与 src/rust/aurora-runtime-broker/src/launch.rs 保持一致：
//   resources/sidecar/python/...              解释器副本
//   resources/sidecar/python/<purelib>/...    依赖，合并进解释器自身的 site-packages

import { spawnSync } from 'node:child_process'
import { chmodSync, cpSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const TAURI_ROOT = fileURLToPath(new URL('..', import.meta.url))
const REPO_ROOT = path.resolve(TAURI_ROOT, '..', '..')
const SIDECAR_DIR = path.join(TAURI_ROOT, 'resources', 'sidecar')
const TMP_VENV = path.join(REPO_ROOT, '.tmp-sidecar-venv')
const DIST_DIR = path.join(REPO_ROOT, '.tmp-sidecar-dist')
const PYTHON_VERSION = process.env.PYTHON_VERSION ?? '3.13'
const IS_WINDOWS = process.platform === 'win32'

function fail(message) {
  console.error(`错误: ${message}`)
  process.exit(1)
}

/** 执行命令；command 返回其标准输出。 */
function run(command, args, { capture = false } = {}) {
  const result = spawnSync(command, args, {
    cwd: REPO_ROOT,
    stdio: capture ? ['ignore', 'pipe', 'inherit'] : 'inherit',
    encoding: 'utf8',
  })
  if (result.error) fail(`无法执行 ${command}: ${result.error.message}`)
  if (result.status !== 0) fail(`${command} ${args.join(' ')} 退出码 ${result.status}`)
  return (result.stdout ?? '').trim()
}

/** 在目录树里查找 venv 的 site-packages（取代 bash 版的 find -maxdepth 4）。 */
function findSitePackages(root) {
  const queue = [{ dir: root, depth: 0 }]
  while (queue.length > 0) {
    const { dir, depth } = queue.shift()
    if (depth >= 4) continue
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue
      const full = path.join(dir, entry.name)
      if (entry.name === 'site-packages') return full
      queue.push({ dir: full, depth: depth + 1 })
    }
  }
  fail(`未在 ${root} 下找到 site-packages`)
}

for (const distribution of ['agent', 'cli']) {
  if (!existsSync(path.join(REPO_ROOT, 'src', distribution, 'pyproject.toml'))) {
    fail(`缺少 Python 分布: src/${distribution}`)
  }
}

// 只清生成物：SIDECAR_DIR 自身留着，否则会连带删掉受版本控制的 .gitkeep。
rmSync(path.join(SIDECAR_DIR, 'python'), { recursive: true, force: true })
rmSync(path.join(SIDECAR_DIR, 'site-packages'), { recursive: true, force: true })
rmSync(TMP_VENV, { recursive: true, force: true })
rmSync(DIST_DIR, { recursive: true, force: true })
mkdirSync(SIDECAR_DIR, { recursive: true })

run('uv', ['python', 'install', PYTHON_VERSION])
const PYTHON_BIN = run('uv', ['python', 'find', PYTHON_VERSION], { capture: true })
// 解释器位置随平台不同（Windows 在 prefix 根、Unix 在 bin/），直接问解释器最稳。
const PYTHON_HOME = run(PYTHON_BIN, ['-c', 'import sys; print(sys.prefix)'], { capture: true })
const PREFIX_SITE_REL = run(
  PYTHON_BIN,
  [
    '-c',
    'import sys, sysconfig, os; print(os.path.relpath(sysconfig.get_paths()["purelib"], sys.prefix).replace(os.sep, "/"))',
  ],
  { capture: true },
)
cpSync(PYTHON_HOME, path.join(SIDECAR_DIR, 'python'), { recursive: true })

run('uv', ['venv', '--python', PYTHON_BIN, TMP_VENV])
const VENV_PYTHON = ['bin/python', 'Scripts/python.exe']
  .map((relative) => path.join(TMP_VENV, relative))
  .find((candidate) => existsSync(candidate))
if (!VENV_PYTHON) fail(`venv 中找不到解释器: ${TMP_VENV}`)

// 必须先构建 wheel 再安装：把源码目录直接交给 uv 会被当作工作区成员做 editable 安装，
// 两个可编辑的 aurora 命名空间会互相遮蔽（agent 的 finder 挡住磁盘上的 aurora/cli），
// 运行时会直接 ModuleNotFoundError。
run('uv', ['build', '--all-packages', '--out-dir', DIST_DIR])
const wheels = readdirSync(DIST_DIR)
  .filter((name) => name.endsWith('.whl'))
  .map((name) => path.join(DIST_DIR, name))
if (wheels.length === 0) fail('uv build 没有产出任何 wheel')
run('uv', ['pip', 'install', '--python', VENV_PYTHON, ...wheels])

// 依赖合并进解释器自身的 prefix：.pth 只在解释器自己的 site-packages 下执行，
// 靠 PYTHONPATH 旁挂的话 pywin32 的 bootstrap 不会运行。
const PREFIX_SITE = path.join(SIDECAR_DIR, 'python', PREFIX_SITE_REL)
rmSync(PREFIX_SITE, { recursive: true, force: true })
cpSync(findSitePackages(TMP_VENV), PREFIX_SITE, { recursive: true })

rmSync(TMP_VENV, { recursive: true, force: true })
rmSync(DIST_DIR, { recursive: true, force: true })

const SIDECAR_PYTHON = path.join(
  SIDECAR_DIR,
  'python',
  IS_WINDOWS ? 'python.exe' : path.join('bin', 'python3'),
)
if (!existsSync(SIDECAR_PYTHON)) {
  fail(`sidecar 里没有解释器: ${SIDECAR_PYTHON}（launch.rs 依赖这个路径）`)
}
// 复制可能不保留可执行位，而 launch.rs 会直接执行它。
if (!IS_WINDOWS) {
  const binDir = path.join(SIDECAR_DIR, 'python', 'bin')
  for (const name of readdirSync(binDir)) {
    if (name.startsWith('python')) chmodSync(path.join(binDir, name), 0o755)
  }
}

// 导入自检：把「打好了但其实 import 失败」挡在构建阶段。pywin32 的 DLL bootstrap 与
// aurora 命名空间合并都曾在这里出过问题，只靠拷贝成功是看不出来的。
run(SIDECAR_PYTHON, ['-c', 'import aurora.agent, aurora.cli; print("导入自检通过")'])

console.log(`\nsidecar 构建完成: ${SIDECAR_DIR}`)
console.log(`解释器:   ${SIDECAR_PYTHON}`)
console.log(`依赖目录: ${PREFIX_SITE}`)
