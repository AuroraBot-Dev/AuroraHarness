# src/frontend（前端层）

Aurora Agent 的界面层：Nuxt 4 + Vue 3 + TypeScript + Naive UI + Pinia，桌面场景下跑 SPA 模式，
产物交给 Tauri 打包。

## 职责边界

属于本层：页面与组件、Pinia store、协议客户端、类型定义、单元测试与 E2E。

不属于本层：

- 不管理 Python 进程——开发期的自动拉起只是一个 dev 模块（见下），桌面期由 `src/tauri` 负责
- 不知道协议帧怎么被 Rust 代理编解码，只依赖 `app/utils/protocol.ts` 与 `runtimeClient.ts`
- 不包含 Rust 代码，Rust 壳与代理在 `src/tauri` 与 `src/rust`

```
src/frontend/
├── app/
│   ├── pages/            # 路由页面
│   ├── components/       # 组件
│   ├── stores/           # Pinia
│   ├── composables/      # useIpc 等
│   ├── types/            # 协议与领域类型
│   └── utils/            # runtimeClient / protocol / normalizers
├── modules/dev-backend.ts# 仅开发期：自动拉起 Python 运行时
├── tests/                # vitest
├── e2e/                  # Playwright
├── patches/              # pnpm 补丁（上游包缺类型导出）
├── pnpm-workspace.yaml   # pnpm 设置：allowBuilds 与 patchedDependencies
└── nuxt.config.ts
```

## 两种传输，同一份协议

| 场景 | 链路 |
|---|---|
| 浏览器开发 | `runtimeClient` 连 WebSocket `ws://127.0.0.1:8765/ws` |
| 桌面应用 | `useIpc` 调 Tauri 命令 `runtime_request`，由 Rust 代理走 stdio |

两条链路收发的是同一套 protocol v1 信封与事件结构，因此页面代码不需要关心当前处于哪种模式。

## 开发

在仓库根先装依赖（`make setup`），然后：

```bash
# 浏览器开发：dev 模块会自动在仓库根执行 uv run aurora runtime --port 8765
pnpm dev

# 桌面开发：Tauri 壳 + 前端热更新
pnpm tauri dev
```

`modules/dev-backend.ts` 的默认行为是「自动拉起运行时」，仓库根由模块位置推导
（`<repo>/src/frontend/modules` 往上三级）。想自己掌控运行时生命周期：

```bash
# 终端 A（仓库根）
uv run aurora runtime --port 8765

# 终端 B
VITE_RUNTIME_WS=ws://127.0.0.1:8765/ws pnpm dev
```

只要设置了 `VITE_RUNTIME_WS`，dev 模块就不再自动拉起运行时。`AURORA_ROOT` 可覆盖推导出的
仓库根，仅在目录布局特殊时才需要。

## 检查与构建

```bash
pnpm lint
pnpm typecheck
pnpm test:coverage
pnpm test:e2e
pnpm generate          # 生成 .output/public，供 Tauri 打包
```

`pnpm tauri` 指向 `src/tauri/scripts/tauri.mjs`——Tauri CLI 默认找不到 `src/tauri`，
该脚本负责把路径显式告诉它，细节见 [`docs/architecture.md`](../../docs/architecture.md)。

## 说明

- 请使用 **pnpm 11**：`patches/` 下的补丁文件在 pnpm 12 上会因更严格的解析而安装失败。
- 版本由 Release Please 以 `aurora-desktop` 为名管理，并同步写入
  `src/tauri/tauri.conf.json` 与 `src/tauri/Cargo.toml`。
