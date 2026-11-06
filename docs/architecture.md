# 架构与分层边界

本文说明 AuroraHarness 的五个层次、它们之间的契约，以及这些边界如何被自动守住。
目录树与快速开始见根 [`README.md`](../README.md)。

## 为什么分这些层

合并前的两个仓库里，前端目录同时装着 Nuxt 界面、Tauri 壳和 Rust 代理，后端目录同时装着
运行时和 CLI。这带来两个具体问题：

1. **Rust 代理无法脱离桌面壳测试**——它直接依赖 `tauri::{AppHandle, Emitter}` 来发事件、
   取资源目录，任何单元测试都得先编译整个 Tauri。
2. **agent 层无法脱离 CLI 使用**——CLI 的 argparse 分发与运行时逻辑混在同一个包里。

因此这次拆分的目标不是「目录好看」，而是让每一层都能被单独编译、单独测试、单独安装。

## 层次与职责

| 层 | 路径 | 语言/技术 | 职责 | 明确不做什么 |
|---|---|---|---|---|
| 前端 | `src/frontend` | Nuxt 4 + Vue 3 + TS | 界面、状态管理、协议客户端、E2E | 不碰进程管理，不知道 Python 怎么启动 |
| Tauri 胶水 | `src/tauri` | Rust + Tauri 2 | 暴露命令、把 `AppHandle` 接成事件桥、钥匙串读写、打包配置 | 不含代理逻辑，不知道怎么收发 NDJSON |
| 纯 Rust | `src/rust` | Rust | 协议 v1 契约、Python 子进程监管、请求/响应关联、密钥脱敏 | 不依赖 tauri 与平台集成库（keyring/dbus），不认识 UI |
| python-agent | `src/agent` | Python | 运行时、委派图、工具、沙箱、存储、传输协议 | 不导入 `aurora.cli` |
| python-cli | `src/cli` | Python | 参数解析、子命令注册、退出码 | 不承载业务逻辑 |

## 依赖方向

```
src/frontend ──(Tauri IPC)──▶ src/tauri ──▶ src/rust ──(stdio NDJSON)──▶ src/cli ──▶ src/agent
      │
      └──────────(WebSocket, 仅开发期)────────────────────────────────────────▶ src/cli ──▶ src/agent
```

反向依赖一律禁止。三条最容易被破坏的边界及其实施方式：

| 边界 | 为什么重要 | 如何守住 |
|---|---|---|
| `src/agent` 不导入 `aurora.cli` | agent 必须能脱离 CLI 独立安装 | `src/agent/tests/test_layering.py` 用 AST 扫描全部源码 |
| `src/rust/aurora-runtime-broker` 不依赖 `tauri` / `keyring` / `dbus` | 代理必须能零系统依赖地脱离桌面壳编译与测试 | CI 里 `cargo tree -p aurora-runtime-broker` 断言依赖树中不含这些 |
| 前端不知道 Python 怎么启动 | 换传输方式不应改动界面代码 | 前端只依赖协议客户端（`app/utils/runtimeClient.ts`）与 IPC 命令 |

`src/tauri/src/bridge.rs` 是唯一允许同时知道 Tauri 与代理两边的地方：它把 `AppHandle`
实现成 `EventSink`、把系统钥匙串实现成 `SecretStore`，然后交给代理。代理只认这两个 trait。
钥匙串实现之所以放在壳里而不是代理里，是因为它是平台集成——Linux 的 Secret Service 后端
要求 dbus，一旦放进代理层，这个「纯」层就会被迫依赖系统库。

Python 运行时的生命周期由 `orchestration/runtime.py` 管理，持久化工作流会话在
`orchestration/workflow_session.py`；核心图的中断驱动放在 `core/graph_runner.py`。
模型客户端由 `model_access/client_factory.py` 构建，终端交互在外层 `cli/interactions.py`。
命名规则见 [`CONTRIBUTING.md`](../CONTRIBUTING.md) 的「Python 模块命名」。

能力策略在 agent 层通过 `CapabilityRegistry` 注入，运行时冻结注册清单，工作流与委派角色解析
上下文和工具。注入工具仍受角色白名单与原确认门约束；不增加对 CLI 或 Tauri 的依赖。
作用域、组合规则与运行能力清单见 [`capability-specialization.md`](capability-specialization.md)。

普通文件工具通过 `aurora.common.file_access` 共用敏感路径守卫，既校验原始路径，也检查
符号链接真实目标。确认工具调用不代表允许直接读取或覆盖 `.env`、私钥和常见凭据目录。
该检查不是 shell、MCP 或其他可信扩展的通用读取隔离；剩余需求与验收状态见
[`requirements-status.md`](requirements-status.md)。

Python 模块的完整职责清单见 [`python-modules.md`](python-modules.md)。协议适配位于
`transport/runtime_api.py`、`transport/persistence_api.py`，平台后端选择在
`sandbox/local_executor.py`，可调用 Agent 注册在 `capability/agent_registry.py`。

## 协议 v1

协议版本号在**三处**必须保持一致，改动时三处同改：

- `src/agent/src/aurora/common/protocol.py` → `PROTOCOL_VERSION`
- `src/rust/aurora-protocol/src/lib.rs` → `PROTOCOL_VERSION`
- `src/frontend/app/utils/protocol.ts`

线上结构（NDJSON / WebSocket 帧完全相同）：

```jsonc
// 请求：前端 → Python
{ "protocol_version": 1, "request_id": "req_x", "method": "session.create", "params": { } }

// 响应：携带同一 request_id
{ "protocol_version": 1, "request_id": "req_x", "ok": true, "result": { } }

// 事件：服务端推送，与请求无关联
{ "protocol_version": 1, "event_id": "evt_x", "type": "run.started", "occurred_at": 0, "payload": { } }
```

判定规则集中在 `aurora-protocol`：带 `request_id` + `ok` 的是响应，带 `event_id` + `type`
的是事件，其余静默丢弃。同一份规则在 Python 侧（`aurora/agent/transport/runtime_api.py`）与前端
（`app/utils/protocol.ts`）各有一份实现。

## 运行取消

前端调用 `run.cancel`，参数为 `session_id`、`run_id`；兼容 `sessionId`、`runId`，同一字段的两种写法不能冲突。接口校验运行归属，缺失会话或运行返回 `SESSION_NOT_FOUND`、`RUN_NOT_FOUND`；当前进程没有持有活动运行时返回 `RUNTIME_UNAVAILABLE`。

`{"accepted": true}` 表示接受取消请求。正在执行的模型、工具或预览阶段在安全点收束，随后由原运行请求返回取消快照并发送 `run.cancelled`；等待用户输入的运行立即取消。已取消运行重复请求仍返回 true，不重复写消息或终态事件；其他已结束运行返回 false，保留原终态。stdio 和 WebSocket 的控制请求不占用四个长运行槽位。

图节点及子角色共享一个取消信号，本机工具轮询该信号并回收进程树。取消后持久化终态、失效未解决交互、关闭未完成任务和角色执行、释放会话及工作区锁。已经完成的任务、消息及文件修改保留；取消不等于 Git 回滚。

同步模型调用当前不能在网络请求中途强行中断，外部 MCP 工作也可能已产生副作用。前端因此显示“正在停止”，直到收到后端终态，不能把 accepted 当成终态。迟到的执行事件、旧运行响应和旧历史快照不得把取消恢复为运行或待审批。跨平台的真实进程及远端服务回收仍需单独验收。

## 审批期限与确认

持久会话中的 `approval` 危险操作默认 300 秒后自动拒绝，期限在创建审批时保存为 UTC `expires_at`。Python 嵌入入口可用 `AgentRuntime(approval_timeout=...)` 设置正有限秒数；改变配置不会重置已存在审批的期限。澄清、评分和普通流程决策不受这个危险操作期限约束。

前端调用 `approval.resolve`，传 `{session_id, run_id, approval_id, approved}`；`approved` 必须是真正的布尔值，字符串和数值不能授权。兼容 camelCase 标识符，冲突别名被拒绝。接口在审批提交后立即确认 `{accepted:true}`，继续执行的进度及终态另发事件，不能把接受请求当作操作已获批准。`run.resume` 保留对旧客户端及其他人工输入的支持，危险操作同样检查期限和布尔值。

`approval.requested` 含 `approval_id`、动作、风险及 `expires_at`；`approval.resolved` 含 `approval_id`、`approved`、`resolved_at`、`resolution`。当前仍发送旧 `approval.required` 作为兼容通知，前端按审批标识合并。解决结果先和审计事件一起提交，再通知传输层。未知审批返回 `APPROVAL_NOT_FOUND`，已解决返回 `APPROVAL_ALREADY_RESOLVED`，过期返回 `APPROVAL_EXPIRED`；会话及运行归属使用相同的稳定校验错误码。

每个运行时最多一个审批调度线程，stdio/WebSocket 连接订阅后台通知；启动请求已返回后无需客户端轮询。到期再次校验数据库中的 pending 状态，批准提交事务也再次校验截止时间。超时、批准和取消在会话控制锁内竞争，只能保存一个解决结果。任何未解决审批到期会拒绝整轮剩余操作并结束为 failed；若另一个已批准操作仍在执行，共用停止信号在安全点收回该执行。超时终态通知在释放执行锁后发出，接收器可立即发起下一轮。已完成任务和写入保留，不重放或回滚。

前端显示倒计时；本地到零先禁用按钮并等待后端结果，提交中也保留卡片。收到解决事件或读取已提交快照后显示明确的批准、拒绝或超时结果，刷新页面保留审批历史。迁移 `003_approval_deadlines.sql` 增加期限及解决元数据；重启后旧审批失效。通过检查点校验的等待运行会换发审批 ID，沿用原截止时间和任务归属。

## 运行与任务重试

`run.retry` 接收 `{session_id, run_id, task_id?}`，兼容对应的 camelCase 标识符。没有 task_id 时重试失败、取消或中断的运行；指定 task_id 时只重试该任务及其子范围。来源必须属于该会话，同一会话仍只能有一个活动运行。来源运行仍活动、任务不属于来源或会话忙时拒绝请求，不改写旧记录。

每次有效请求创建新的运行 ID、消息、角色执行、任务、审批和 Git 基线。运行保存 `retry_of_run_id`、`retry_task_id`，任务保存 `retry_of_task_id`；创建事务提交后立即返回 `{run}`，后续进度和终态通过原有运行事件推送。创建确认中断后可以通过 session.get 找到实际创建的运行，不能因响应丢失而把来源改成运行中或失败。

已有执行计划从 SQLite 按原任务插入顺序恢复，保留来源调度方式，跳过已记录为 completed 的兄弟任务。委派任务包含原有子任务时，只执行其未完成部分；源执行计划尚未产生时才重新规划。只读 say/plan 重试重新走对应模型路径，不能把没有工具执行记录的规划预览当作空计划返回。原已完成结果作为同角色的有界上下文保留，连续重试仍保留角色路径、来源链及子树特化，不能把子角色提升为共享父角色私有上下文。指定已完成任务属于显式重新执行该范围；界面默认只提供失败、取消及中断任务的重试入口。

重试保留原流程结构，重新读取相同角色当前的模型、指令和工具权限，以及会话当前的沙箱与审批策略。停用角色或撤回的委派权限不能被旧快照恢复；沙箱策略改变时重建绑定工具。图片产物检查来源、哈希和当前模型能力，再复制为新运行拥有的证据并记录原产物 ID。整个流程的编写阶段已完成时保留工作区内容，重新截图和审查；单任务重试只验证该范围，不自动宣称原运行已完成。

命令工具遇到非零退出或超时会记录任务失败；不能把包含“退出码 1”的字符串当作成功。重试不会恢复旧 Git 工作树，也不能保证失败任务已经产生的部分写入或远端副作用只发生一次。当前已覆盖受限递归树的整轮、子树与叶子重试，以及重启后的新运行重试；等待人工输入时可在原运行续行，工具正在执行时发生中断则保留历史供重试。显式依赖计划会把所选任务缺少的前置步骤一并纳入重试。

## Python 子进程的三种启动方式

`src/rust/aurora-runtime-broker/src/launch.rs` 按优先级决定怎么拉起运行时：

| 优先级 | 条件 | 命令 | 工作目录 |
|---|---|---|---|
| 1 | 设置了 `AURORA_SIDECAR` | 该可执行文件 `--stdio` | 可执行文件所在目录 |
| 2 | `resource_dir/sidecar/python` 存在 | 内置解释器 `-m aurora.cli.main runtime` | 资源目录 |
| 3 | 开发态回退 | `uv run --no-sync aurora runtime` | 仓库根（`AURORA_ROOT` 可覆盖） |

开发态回退的工作目录由 `repo_root_from("<repo>/src/tauri")` 推出仓库根——`uv` 必须在
uv 工作区根执行才能解析 `aurora` 命令。

第二种方式（bundle 内置解释器）的目录约定见 [`src/tauri/README.md`](../src/tauri/README.md)：
Tauri 将 `resources/sidecar/` 显式映射到资源根下的 `sidecar/`，与代理查找路径保持一致。
依赖必须合并进解释器自身的 `site-packages`，不能靠 `PYTHONPATH` 旁挂——`.pth` 只在解释器
自身的 `site-packages` 下执行，而 pywin32 的 DLL bootstrap 正是 `.pth`。

## Tauri CLI 如何定位壳工程

Tauri CLI 只会在当前目录及其**最多三层子目录**里寻找 `tauri.conf.json`，而本仓库把壳放在
`src/tauri`、前端放在 `src/frontend`，两者互不为子目录。因此：

- 本地：`pnpm tauri` 实际执行 `src/tauri/scripts/tauri.mjs`，它显式设置
  `TAURI_APP_PATH` / `TAURI_FRONTEND_PATH` 后再调用 CLI。
- CI：`tauri-action` 的 `projectPath` 设为 `src/tauri`——以该目录为工作目录时，CLI 的
  默认探测恰好能找到 `tauri.conf.json`，并向下找到 `src/frontend/package.json`。

`src/tauri/tauri.conf.json` 里跨层的路径都写成了相对路径：

```jsonc
"beforeDevCommand":  { "script": "pnpm dev",      "cwd": "../frontend" },
"beforeBuildCommand":{ "script": "pnpm generate", "cwd": "../frontend" },
"frontendDist": "../frontend/.output/public"
```

## 工作区布局

仓库根同时是两种工作区的根，这是刻意的：让 `uv run` / `cargo` 从任意子目录都能解析到同一份
锁文件与构建缓存。

```
pyproject.toml     uv 虚拟工作区根（无 [project]）      → uv.lock, .venv/
Cargo.toml         cargo 工作区根                       → Cargo.lock, target/
src/frontend/      pnpm 包（pnpm-workspace.yaml 在此）   → pnpm-lock.yaml
pyrightconfig.json Python 类型检查（只覆盖两层源码）
lefthook.yml       唯一的 Git 钩子来源（git 根）
```

## 平台差异（跨平台约定）

这套代码要同时跑在 macOS / Windows / Linux 上，且历史上主要在 macOS 上开发。以下约定是
踩过坑之后固定下来的，改动时请保持：

| 事项 | 约定 |
|---|---|
| 命令串的执行者 | 沙箱执行器把命令串交给**平台解释器**：类 Unix 用 `/bin/sh -c`，Windows 用 `powershell -Command`。两者引号规则不同——POSIX 单引号在 PowerShell 下是非法表达式，带空格的程序路径还需要调用运算符 `&`。 |
| 进程树回收 | 类 Unix 用 `killpg` 杀掉整个进程组；Windows 没有进程组信号，改为 `taskkill /F /T /PID`。只 `kill()` 直接子进程会留下孙子进程（命令解释器拉起的服务），它们持有管道会让排空线程永久阻塞。 |
| 沙箱后端 | Linux：bubblewrap / Landlock；macOS：Seatbelt；Windows：受限令牌 + NTFS ACL + Job Object。各自按 `sys.platform` 分支，互不干扰。 |
| Python 解释器位置 | Windows 的 `prefix` 根下就是 `python.exe`，类 Unix 在 `bin/` 下。打包 sidecar 时用 `sys.prefix` / `sysconfig` 询问解释器，不要靠目录层级猜。 |
| 依赖注入位置 | 依赖必须落在解释器自身的 `site-packages`：`.pth` 只在自身的 site-packages 下执行（pywin32 的 DLL bootstrap 依赖它）。 |
| sidecar 独立性 | 使用 `uv python find --managed-python --system --no-project --resolve-links` 排除项目 `.venv`；复制时保留相对符号链接，禁止解释器链接指回开发机。构建后验证 `sys.prefix` 与 `sys.base_prefix` 都位于 sidecar。运行时依赖使用根 `uv.lock` 的冻结约束。 |
| 类型检查平台 | `pyrightconfig.json` 固定 `pythonPlatform: Linux`，与 CI 一致，避免在 Windows 本地因 `os.O_CLOEXEC` 这类 Unix 专有 API 误报。要查 Windows 语义可临时加 `--pythonplatform Windows`。 |
| 文本文件 I/O | 一律显式 `encoding="utf-8"`。不指定就用宿主 locale 编码：macOS / Linux 是 UTF-8，而英文 Windows 是 cp1252，中文内容在那边会直接抛 `UnicodeEncodeError`，中文路径还会解成乱码。`src/agent/tests/test_portability.py` 用 AST 扫描守住这条约定。 |
| HTTP 探测 | 探测本机服务（预览服务等）必须绕开系统代理：`ProxyHandler({})`。不少 CI 与公司网络设置了 `HTTP_PROXY`，否则连 `127.0.0.1` 也会被转发出去。 |
| CI 覆盖 | Python 与 Rust 都跑 ubuntu / windows / macos 三平台矩阵。**改动平台相关代码后不要只看自己那台机器。** |

## 计划校验与结构上限

`core/plan_validation.py` 在派发前一次验证整份计划：列表结构、非空且唯一的任务 ID、描述、强度、当前角色授权工具、JSON 参数、函数签名和完整工具 JSON Schema。计划最多 1 MiB；远程 schema 引用禁止在校验时发起网络请求，本地引用可用。整数 ID 显式转为字符串后检查冲突；布尔值、空值和无效模型响应明确拒绝。普通空计划不能被当成成功，只有已保留完成工作的重试允许内部空计划。

模型规划器与自定义规划器共用校验，`plan` 模式同样验证；后续任务无效时，前面的合法工具也不会先执行。MCP 调用目标通过闭包固定，模型参数不能覆盖远端工具名。工具声明和原有直接 `Tool.run` 参数转换接口保持分离。

```python
from aurora.agent.core import ExecutionLimits
from aurora.agent.orchestration.runtime import AgentRuntime

runtime = AgentRuntime(
    execution_limits=ExecutionLimits(
        max_depth=4,
        max_plan_tasks=8,
        max_total_tasks=128,
    )
)
```

SDK 允许深度 0–16、单份计划 1–256、整轮任务 1–4096。根深度为 0；0 禁止进一步委派。整轮计数包含委派任务和普通叶子，澄清重规划替换同角色预留，审批恢复不重复计数，视觉返修的新角色执行继续消耗同一预算。新运行和重试冻结当时配置，正在等待审批的运行保留原限制。最大支持深度 16 有实际执行测试，摘要逐层提取子结果正文，避免递归 JSON 转义指数增长。

错误如 `EMPTY_PLAN`、`DUPLICATE_TASK_ID`、`TOOL_NOT_ALLOWED`、`INVALID_TOOL_ARGUMENTS`、`PLAN_TASK_LIMIT`、`RUN_TASK_LIMIT`、`DELEGATION_DEPTH_LIMIT` 会在失败终态保存后返回。结构上限代码还写入运行公开状态的 `error_code`。深度超限在创建下一层模型前拒绝；总任务数或计划校验失败在任何新叶子执行前拒绝。

配置目前由 Python SDK 提供，尚无设置页入口；整轮时长、token、成本预算及任意执行节点的持久恢复仍未完成，不能将结构限制等同于全部资源控制。

## 任务依赖与重试范围

新计划通过 `depends_on` 声明同一角色计划内的前置任务，独立任务填写空列表。所有引用在审批前统一校验，允许引用后面列出的步骤。就绪任务按批次执行。只读叶子和可以从冻结白名单证明只读的子树可同时运行，写入、命令或权限范围不确定的委派独占一个批次。未声明依赖的旧计划保留原派发语义。

例如，准备文件失败会阻断依赖它的发布步骤，独立检查仍可完成。被阻断的步骤记录直接阻断来源，跳过审批与工具调用。重试发布时只带上尚未完成的前置步骤。历史快照、实时任务事件与抽屉使用相同的持久任务 ID。

同一运行时按 Git 工作树根目录协调会话。同一个工作树或相互包含的工作树同一时间只允许一个会话执行或回滚，不相交的工作树可并行。运行等待输入时冻结本轮差异并释放占用。如果另一个会话正在使用工作树，审批响应返回 `RUN_BUSY`，原审批和期限保留。可见文件、索引或分支在等待期间改变时，原运行中断，可基于当前文件重试。取消或超时使用冻结时的差异，防止把后来其他会话的写入归入本轮回滚范围。

## 已知的偏离与待办

诚实记录当前状态，避免把它们当成「已解决」：

- 协议版本号仍是三处手写常量，尚未做生成或校验（见 `docs/adr/001-protocol-version.md`）。
- `preview/browser.py` 等待本机预览服务就绪的时限**硬编码为 20 秒**，且不可配置。实测在 macOS CI 上
  满载跑测试时，同一台机器上 `python -m http.server` 的启动会超过这个时限（空闲时约 6 秒），
  表现为「预览服务启动超时」；真实用户的 dev server 冷启动也可能更久。建议改成可配置项并给出
  更宽松的默认值。
- 浏览器预览测试只在 ubuntu 上执行（那里会安装 Chromium）；Windows / macOS 上由于没有浏览器
  而跳过，桌面壳在那边只做 `cargo check`，真正的安装包构建在 tag 触发的 release 流水线里做。
- `docs/notes/` 下的两份文档记录了合并前两个仓库的工程化过程，其中的目录约定与命令已经
  过时，保留作为历史参考。
