# Python 模块职责清单

本清单覆盖 `src/agent/src`、`src/cli/src` 及两层测试。环境、缓存、第三方依赖和构建产物不属于源码命名审查范围。

模块采用小写 `snake_case`，名称以所在包的领域和具体职责共同确定。包的 `__init__.py` 用于公共导出；CLI 主入口保留 `main.py`，子命令模块使用命令对应的名称。`base.py`、`config.py`、`state.py` 只在包领域足够明确且内容与名称相符时保留。

## Agent 层

以下路径相对 `src/agent/src/aurora/agent`：

| 包 | 模块 | 职责与命名理由 |
| --- | --- | --- |
| `capability` | `agent_registry.py` | 可调用 Agent 发现、配置校验与连接测试，区别于能力策略注册 |
| `capability` | `specialization.py` | 能力策略、作用域、优先级解析和组合 |
| `conversation` | `session.py` | 连续对话与斜杠指令，包路径已限定对话语义 |
| `core` | `graph.py` | 构建委派状态图 |
| `core` | `plan_validation.py` | 派发前完整校验计划、授权工具与参数 schema |
| `core` | `execution_limits.py` | 结构上限与整轮共享任务预留 |
| `core` | `effort_policy.py` | 每档提示、模型输出限制与委派规划上限 |
| `model_access` | `token_usage.py` | 从模型响应读取实际报告的 token 字段 |
| `orchestration` | `model_usage.py` | 分来源保存角色的规划与回答用量 |
| `core` | `graph_runner.py` | 驱动图执行及人工中断恢复 |
| `core` | `planner.py` | 规划与澄清协议、启发式强度分类 |
| `core` | `llm_planner.py` | 模型实现的规划和澄清 |
| `core` | `state.py` | 委派图状态与任务、结果等数据模型 |
| `mcp` | `client.py` | MCP stdio 会话客户端 |
| `mcp` | `config.py` | `McpServerConfig` 配置结构与校验 |
| `mcp` | `tools.py` | 将远端 MCP 工具适配为 Aurora 工具 |
| `mcp/packages` | `base.py` | 功能包清单与基础契约 |
| `mcp/packages` | `loader.py` | 从 YAML 目录加载功能包 |
| `mcp/packages` | `registry.py` | 功能包发现与注册，包路径明确注册对象 |
| `mcp/packages/blender`、`qq` | `__init__.py` | 声明内置功能包目录 |
| `model_access` | `client_factory.py` | 根据环境变量或冻结配置构建模型客户端 |
| `orchestration` | `runtime.py` | 运行时生命周期、会话资源、MCP 连接及工作区校验 |
| `orchestration` | `workflow_session.py` | 一条持久化工作流会话的编写、审查与汇总 |
| `orchestration` | `delegation.py` | 父运行内的子 Agent 执行与审批恢复 |
| `orchestration` | `approval_manager.py` | 审批期限、解决记录及单线程到期调度 |
| `orchestration` | `runtime_events.py` | 请求结束后仍可发送的运行时通知及连接订阅 |
| `orchestration` | `retry_plan.py` | 从历史构造重试范围、保留完成结果及复制来源证据 |
| `orchestration` | `tool_tracing.py` | 记录真实工具调用、耗时、终态及脱敏输入输出 |
| `orchestration` | `task_execution.py` | 登记计划、阻断来源和执行结果，判断只读委派能否并发 |
| `orchestration` | `task_events.py` | 提交后发送带父任务 ID 的子树状态变化 |
| `orchestration` | `workflow_checkpoints.py` | 持久化等待点，核对恢复条件，换发待处理输入 |
| `orchestration` | `graph_checkpoint.py` | 编解码根图、子图和已完成并行任务的位置 |
| `preview` | `browser.py` | 预览校验、服务等待和浏览器截图 |
| `safety` | `gate.py` | 工具调用确认门及放行策略 |
| `sandbox` | `sandbox.py` | 工作区路径、文件读写和命令执行门面 |
| `sandbox` | `executor.py` | 执行协议、执行结果及共享进程管理 |
| `sandbox` | `local_executor.py` | 选择并驱动平台隔离后端 |
| `sandbox` | `landlock_runner.py`、`windows_runner.py` | 可直接由模块路径启动的平台限制程序 |
| `store` | `database.py` | SQLite、事务与迁移 |
| `store` | `records.py` | 业务记录、配置快照与历史读写 |
| `store` | `ownership.py` | 同一数据库的运行时独占持有 |
| `store` | `git.py` | Git 工作树状态、差异和快照回滚 |
| `store/model` | `base.py`、`entities.py` | ORM 基类与业务实体 |
| `tools` | `base.py` | 工具抽象、参数结构、风险与声明式注册 |
| `tools` | `builtin.py` | 内置只读工具 |
| `tools` | `sandbox_tools.py` | 绑定具体工作区沙箱的工具 |
| `transport` | `runtime_api.py` | 共用运行时请求、响应、事件与 NDJSON 接入 |
| `transport` | `persistence_api.py` | 配置、会话历史和产物的协议接口 |
| `transport` | `websocket.py` | WebSocket 传输接入 |

`aurora/common` 下的 `logging.py`、`protocol.py`、`text.py` 分别负责日志脱敏、协议版本和 Unicode 文本清洗，`file_access.py` 统一文件工具的敏感路径检查，`cancellation.py` 提供跨工作流与执行器共用的协作取消信号和安全点。`aurora/eval/feedback.py` 保存评分及调用轨迹，`cache/__init__.py` 保留独立缓存包入口。

## CLI 与测试

`src/cli/src/aurora/cli/main.py` 注册命令并分发参数，`interactions.py` 收集审批、澄清和评分。`commands/` 中的 `demo.py`、`evaluate.py`、`ruff.py`、`sandbox.py`、`serve.py`、`runtime.py` 对应各自子命令，不承载 Agent 业务逻辑。

测试使用 `test_<被测领域>.py` 命名，按被验证行为组织；`tests/fixtures/mcp_server.py` 是可由子进程直接启动的 MCP 测试服务。测试中的绝对导入与模块路径调用需要随重命名同步更新。

## 兼容与构建验证

公共包导出 `aurora.agent.core`、`aurora.agent.sandbox`、`aurora.agent.transport`、`aurora.agent.model_access` 保持原有入口。`aurora.cli.main`、`aurora.agent.sandbox.landlock_runner` 和 `aurora.agent.sandbox.windows_runner` 等进程入口维持不变，避免破坏安装包和平台启动命令。

审查后的检查包括 Python 语法与导入扫描、Ruff、Pyright、三层测试、wheel/sdist、前端静态构建和桌面安装包。sidecar 额外验证打包后导入、CLI 启动、协议初始化和会话创建，以覆盖只在非 editable 安装中才会暴露的模块缺失。
