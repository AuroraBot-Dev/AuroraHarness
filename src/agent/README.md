# aurora-agent（agent 层）

形如 CodeX 的全平台桌面 AI Agent 应用 —— 用户把项目交给 Agent，Agent 会自主规划、委派并完成开发任务。

> 完整的设计与使用文档见：[AuroraAgent-demo 文档站](https://haha-ha-cuo.github.io/AuroraAgent-demo/)

本目录是 AuroraHarness 的 **agent 层**，分布名 `aurora-agent`：运行时、委派图、工具、沙箱、存储与传输协议都在这里。命令行入口在 `src/cli`（分布名 `aurora-cli`），它单向依赖本层；本层不导入 `aurora.cli`，因此可以脱离 CLI 独立安装使用。分层全貌见 [`docs/architecture.md`](../../docs/architecture.md)。

> 下文所有 `uv run ...` 都在**仓库根**执行——根目录才是 uv 工作区所在。

## 项目定位

传统编码助手以「单轮对话 + 单点补全」为主，Aurora 要交付的是一个可长时间自主运行的项目级 Agent：

- **树形委派**：根 Agent 顶层规划 → 拆解 → 并行派发子 Agent → 汇总
- **推理强度**：按任务复杂度在 low / medium / high 三档间权衡成本与质量
- **能力特化层**：用户可插拔地注入上下文、工具、提示与检索策略
- **全平台分发**：最终形态是跨 macOS / Windows / Linux 的桌面应用（Tauri 2 + Nuxt）

## 当前状态

参考文档路线图，当前处于 **阶段二（Agent 核心）→ 阶段三（桌面壳 MVP）** 的过渡期。

**已实现（Python 运行时，最小纵向切片）**

- 最小委派图：LangGraph 状态图 + Send 并行派发（plan → dispatch → execute → summarize）
- 推理强度三档（low / medium / high）+ mock 确定性分档
- 工具层：list_files / read_file + 风险分级（read / write / execute）
- 沙箱：按平台使用 Bubblewrap / Landlock / Seatbelt / Windows 受限令牌限制本机进程写入
- 确认门：read 放行、write / execute 默认拒绝，支持交互确认 / 全放行 / 只读三种策略
- 统一彩色日志（rich 的 RichHandler）
- CLI 入口（`uv run aurora demo` / `uv run aurora sandbox`）

**规划中**

- 本地 API 管理层（mock 客户端 + 真实 OpenAI 兼容 API）
- 能力特化层 / 缓存层 / MCP 集成
- SQLite 持久化 / 运行时协议（stdio NDJSON）
- 桌面壳（Tauri 2 + Nuxt）与 Python sidecar 打包

## 技术栈

| 组件 | 选型 |
|---|---|
| Python | 3.13（uv 管理） |
| 编排 | langchain-core + langgraph（不引入 langchain 元包，见 ADR-003） |
| 终端美化 / 日志 | rich |
| 测试 | pytest |
| 桌面壳（规划） | Tauri 2 + Nuxt（Vue 3 + TypeScript，SPA） |

## 目录结构

```
src/agent/                     # 分布：aurora-agent
├── pyproject.toml             # name = aurora-agent
├── src/aurora/
│   ├── protocol.py            # 协议版本（与 Rust / 前端三处必须一致）
│   ├── logging.py             # 统一彩色日志（RichHandler）
│   ├── text.py                # 文本清洗与脱敏辅助
│   ├── agent/
│   │   ├── core/              # 委派图 + 规划器 + 推理强度
│   │   ├── tools/             # 工具抽象 + 内置工具 + 沙箱工具 + 风险分级
│   │   ├── safety/            # 确认门 / 安全权限模型
│   │   ├── sandbox/           # 沙箱：隔离工作区 + 受限执行后端
│   │   ├── model_access/      # 本地 API 管理层
│   │   ├── mcp/               # 目录式 MCP 功能包、插件注册表、Client 与工具适配
│   │   ├── store/             # SQLite 持久化
│   │   └── transport/         # 运行时协议 / stdio NDJSON / WebSocket
│   └── eval/                  # 评估与校准
└── tests/                     # pytest 测试

src/cli/                       # 另一个分布：aurora-cli，只做参数解析与命令分发
```

## 快速开始

### 环境准备

- [uv](https://docs.astral.sh/uv/)（Python 环境与依赖管理）
- Python 3.13

### 安装

在仓库根执行，一次装好 `aurora-agent` 与 `aurora-cli` 两个分布：

```bash
uv sync
```

格式化全部 Python 代码：

```bash
uv run aurora ruff
uv run aurora ruff --check
uv run aurora ruff src tests
```

真实 API 密钥只能保存在被 Git 忽略的 `.env` 或系统钥匙串中，绝不要提交到仓库。

### 运行最小 demo（mock 模式，无需密钥）

```bash
uv run aurora demo
# 或指定目标
uv run aurora demo "列出项目文件结构"
```

默认目标为「列出项目文件结构，并阅读 README.md 总结项目定位」，会规划出子任务、并行派发并输出汇总报告。

### 启动持续会话

```bash
uv run aurora serve
uv run aurora serve --mode read-only --approve never
uv run aurora serve --feedback-file .aurora/eval-feedback.jsonl
uv run aurora serve --sandbox-dir /path/to/project
```

服务启动后支持：

```text
/say <内容>    直接对话并保留多轮上下文；普通文本等同于 /say
/plan <目标>   生成工具执行计划，但不执行
/run <目标>    规划并执行目标
/clear         清空直接对话上下文
/help          显示帮助
/exit          停止服务
```

`serve` 默认以启动命令时的当前目录作为工作区，也可通过 `--sandbox-dir` 指定项目目录。`/say` 和 `/plan` 不执行工具；`/run` 使用启动参数指定的确认门与本机沙箱。规划信息不足时会暂停询问并带回答重新规划；执行结束会展示调用轨迹。指定 `--feedback-file` 后还会询问 1-5 分和文字评价，并追加保存到 JSONL 评估集。

### 启动桌面前端运行时

```bash
uv run aurora runtime
```

运行时通过 stdin/stdout 交换逐行 JSON，不监听本地端口。前端先调用 `workspace.validate` 校验系统目录选择器返回的路径；非 Git 工作区会通过 `workspace.git.initialize` 自动初始化，再通过 `session.create` 创建绑定到该工作区的独立 Agent 会话。

浏览器开发模式可启用 WebSocket 传输：

```bash
uv run aurora runtime --port 8765
```

WebSocket 地址为 `ws://127.0.0.1:8765/ws`。`src/frontend` 的 `pnpm dev` 会自动执行这条命令（工作目录为仓库根），无需手动启动。

```json
{"id":"1","method":"workspace.validate","params":{"path":"/path/to/project"}}
{"id":"2","method":"session.create","params":{"workspacePath":"/path/to/project","sandboxMode":"workspace-write","approvalMode":"interactive"}}
{"id":"3","method":"run.start","params":{"sessionId":"<session-id>","goal":"查看 Git 提交记录"}}
{"id":"4","method":"git.status","params":{"sessionId":"<session-id>","view":"workspace"}}
{"id":"5","method":"git.diff","params":{"sessionId":"<session-id>","view":"run","runId":"<run-id>","path":"src/app.py"}}
{"id":"6","method":"git.rollback","params":{"sessionId":"<session-id>","runId":"<run-id>"}}
```

每轮 Agent 运行前后都会生成会话级临时 Git 快照。桌面端可以分别查看本轮变更和工作区相对 HEAD 的变更，并在工作区未继续变化时回滚最近一轮；运行前已有的未提交内容会被保留。快照不会创建提交、分支或 stash，随会话关闭清理。

交互审批、目标澄清和结果评价分别通过 `approval.required`、`clarification.required` 和 `evaluation.required` 事件通知前端。前端使用事件中的 `sessionId`、`runId` 和 `interruptId` 调用 `run.resume`，完成后运行时广播 `run.completed`。

运行过程中会实时推送 `run.started`、`plan.created`、`task.started`、`task.completed` / `task.failed`，最终回答通过 `message.started`、`message.delta`、`message.completed` 增量发送。stdio 与 WebSocket 使用完全相同的事件结构。

桌面运行时可以先读取功能包目录，再在创建 Agent 会话前连接 Blender、QQ 等能力：

```json
{"id":"m1","method":"mcp.package.catalog"}
{"id":"m2","method":"mcp.package.connect","params":{"packageId":"blender","config":{}}}
{"id":"m3","method":"mcp.package.list"}
```

QQ 包兼容不同 MCP Server，因此需要传入具体实现的启动命令：

```json
{"id":"q1","method":"mcp.package.connect","params":{"packageId":"qq","instanceName":"work-qq","config":{"command":"<已安装的 QQ MCP Server>","args":[],"env":{"QQ_BOT_TOKEN":"..."}}}}
```

连接成功后，Server 工具会以 `mcp.<instance>.<tool>` 注册到后续创建的 Agent 会话。MCP 明确标注 `readOnlyHint=true` 的工具按只读处理，其余工具默认需要执行审批；各功能包还能强化自身的风险策略。`mcp.server.*` 仍可作为底层调试接口。断开连接会关闭 SDK 会话并回收由 stdio transport 启动的子进程。

一个软件对应 `mcp/packages/<软件>/` 目录，目录内的 `package.yaml` 声明软件信息、Server 启动配置、前端配置 Schema，以及功能到远端 MCP Tool 的映射和风险级别。例如 QQ 的 `message.send` 映射 `send_*`，Blender 的 `scene.modify` 映射对象修改工具。新增内置软件只需增加目录；第三方包可通过 `aurora.mcp_packages` entry point 返回同结构的目录路径，后续可以独立发布为插件而无需修改 Aurora 核心代码。

### 运行沙箱命令（让 Agent 写代码并运行）

```bash
uv run aurora sandbox "在沙箱里写一个计算斐波那契数列的脚本并运行"
# 确认门默认交互式；write / execute 会逐个询问 y/N
uv run aurora sandbox --approve always "..."   # 全放行（危险）
uv run aurora sandbox --approve never "..."    # 只读，写与执行一律拒绝
uv run aurora sandbox --sandbox-dir /tmp/mybox "..."  # 自定义沙箱目录
uv run aurora sandbox --mode read-only "..."   # 文件系统只读
uv run aurora sandbox --mode workspace-write "..."  # 默认，仅工作区和临时目录可写
uv run aurora sandbox --mode danger-full-access "..."  # 无隔离（危险）
```

### 本机沙箱后端

Aurora 会功能探测并按平台选择后端：

- Linux：优先 `bwrap`，不可用时使用内置 Landlock runner；可用包管理器安装 `bubblewrap` 获得更完整的挂载隔离。
- macOS：使用系统自带的 `sandbox-exec` / Seatbelt。
- Windows：使用 `WRITE_RESTRICTED` 受限令牌、NTFS ACL 与 Job Object；`uv sync` 会按平台安装 `pywin32`。

受限模式找不到可用后端时会拒绝执行，不会静默降级到普通子进程。该沙箱面向本地个人 Agent，约束文件写入而不限制读取、网络和进程可见性；不要把它作为公网多租户代码执行边界。

### 日志级别

默认 INFO，可用环境变量调整：

```bash
AURORA_LOG_LEVEL=DEBUG uv run aurora demo
```

## 参考文档

设计文档、架构决策（ADR）与路线图见 [AuroraAgent-demo](https://haha-ha-cuo.github.io/AuroraAgent-demo/)。

仓库内决策记录见 [`docs/adr/`](../../docs/adr/README.md)。

## 开发指南

贡献流程、分支和提交规范见仓库根的 [`CONTRIBUTING.md`](../../CONTRIBUTING.md)。提交钩子由 lefthook 统一管理（配置在仓库根 `lefthook.yml`），改动 Python 文件时会自动跑 ruff 与 pyright。

想手动全量跑一遍：

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## 测试

```bash
uv run pytest
uv run pyright
uv run ruff check .
uv run pip-audit
```

测试默认收集分支覆盖率并要求总覆盖率不低于 60%。

## 发布

版本遵循 SemVer。合并 Conventional Commits 后，Release Please 按包维护各自的版本 PR 与 `CHANGELOG.md`；合并后产生的 `aurora-agent-v*` / `aurora-cli-v*` tag 会触发 wheel/sdist、GitHub Release 和 PyPI Trusted Publishing。

## 常见问题

- `uv` 缺失：按照 uv 官方文档安装后重新运行 `uv sync --frozen`。
- API 配置失败：从 `.env.example` 复制 `.env` 并填写模型、端点和密钥。
- 8765 端口占用：关闭已有开发运行时，或显式设置 `VITE_RUNTIME_WS` 后手动管理后端。

## 许可证

本项目采用 [MIT License](../../LICENSE)。

## 数据库与多 Agent 协作

本地历史、模型与 Agent 配置、预设协作流程和视觉审查记录由 SQLite 管理。配置步骤、数据结构、接口及重启行为见 [数据库与协作说明](../../docs/database.md)。视觉审查首次使用前运行 `uv run playwright install chromium`。
