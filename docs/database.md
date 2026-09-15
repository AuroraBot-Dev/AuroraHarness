# 本地数据库与多 Agent 协作

Aurora 使用 Python 后端管理 SQLite，桌面端与浏览器端通过协议版本 1 访问。每个会话选择一个预设流程，每轮运行冻结流程、Agent、模型、工具权限和预览配置。多个角色在同一个工作区串行执行。

## 启动和配置

```sh
uv sync
uv run playwright install chromium
uv run aurora runtime --port 8765
```

数据库默认位置：macOS 为 `~/Library/Application Support/Aurora/aurora.db`，Windows 为 `%LOCALAPPDATA%/Aurora/aurora.db`，Linux 为 `$XDG_DATA_HOME/Aurora/aurora.db`（默认 `~/.local/share`）。通过 `AURORA_DATABASE_PATH` 指定其他位置。一个数据库同时只允许一个运行时持有；桌面端和独立 CLI 不应同时接管同一个数据库。

初次启动从环境变量导入默认配置，未设置模型时保存 `unconfigured`，仍可浏览配置和历史，但执行前必须配置有效模型。数据库已有配置时不会被 `.env` 覆盖。

在前端设置页依次添加供应商、模型、Agent 和协作流程：

1. 供应商设置 OpenAI 兼容端点。凭据可使用 `env:VARIABLE_NAME`；填写 API Key 时通过系统钥匙串保存，数据库记录 `keyring:供应商UUID`。
2. 模型记录服务端模型名称、参数及输入能力。视觉审查角色必须使用声明支持图片且服务端确实支持图片的模型。
3. Agent 分别设置职责与工具白名单；`*` 表示全部，空列表表示不允许调用工具。审查与汇总阶段始终不执行工具。
4. 流程选择单 Agent 或前端视觉审查，绑定编写与审查 Agent。默认最多返修两轮。
5. 会话顶部可切换下一轮使用的流程。首次需要截图时，通过表单填写本机预览 URL、启动命令、工作区内目录和页面。

预览命令经过审批并使用会话沙箱；截图使用独立 Chromium 上下文，默认视口 1440×900、390×844。命令留空时连接已经运行的服务。预览命令设置 90 秒生命周期上限，截图结束立即取消并清理所启动的进程。缺少浏览器、启动失败、截图失败或图片能力不匹配都会记录 `unable_to_review`，不会当作通过。

## 实体和约束

SQL 是完整的结构定义：`src/agent/src/aurora/agent/store/migrations/001_initial.sql`。

- `projects`、`sessions`、`messages` 管理工作区及对话；消息按会话内序号排序，分为公共消息和角色内部交接。
- `model_providers` → `model_configs` → `agents` 管理连接与角色；`workflows`、`workflow_steps` 固定阶段与角色绑定。
- `runs` 表示一次用户请求；`agent_runs` 表示特定角色在特定阶段、轮次的执行；`tasks` 表示该执行中的工具任务。
- `artifacts` 保存截图、代码差异及预览日志的路径、哈希和来源；文件在数据库旁的 `artifacts/运行UUID/` 中。产物读取验证路径归属和 SHA-256。
- `reviews`、`review_artifacts`、`review_findings` 记录审查结论、证据及问题，不覆盖旧轮次。问题只可引用本轮截图，历史问题关联只可指向同一运行。
- `interactions` 保存审批、澄清、评价和返修决策；`run_events` 保存有序阶段轨迹；其余表用于设置、迁移和旧数据导入。

唯一约束保证：同会话一个活动运行、请求键去重、阶段轮次不重复、工具任务 ID 不跨 Agent 执行冲突。外键启用，写事务使用 `BEGIN IMMEDIATE`；模型调用和工具运行不持有写事务。迁移在单一事务中更新结构和版本，失败一起回滚。

同工作区的执行使用互斥锁；从编写完成到截图前、截图结束后检查 Git 工作树标识，变化时拒绝使用失配证据。原有 Git 临时快照仍仅在会话资源存活期间可回滚。

## 协议接口

`runtime.initialize` 返回 `databasePath`、`defaultWorkflowId` 及能力列表。

配置接口为 `project/provider/model/agent/workflow.list/create/update`。列表返回 `{items: [...]}`；创建传 `{data: {...}}`；更新传 `{id, data: {...}}`。`data` 使用数据库对应的 snake_case 业务字段，JSON 字段省略 `_json` 后缀并传结构化值；返回实体的外层字段使用 camelCase。

```json
{"method":"workflow.create","params":{"data":{"name":"前端协作","kind":"frontend-review","code_agent_id":"<编写Agent UUID>","review_agent_id":"<审查Agent UUID>","max_revisions":2}}}
```

其他新增接口：

- `session.list/get/update/delete/clear`：`sessionId` 定位会话；更新支持 `title`、`workflowId`、`archived`。`session.close` 只释放资源，不删除记录。
- `session.create`：保留 `workspacePath`、沙箱和审批配置，增加 `title`、`workflowId`。
- `run.start`：`sessionId`、`goal`、可选 `workflowId`、`requestKey`、`mode`（`run/say/plan`）。同一请求键返回已登记运行，不再次执行。
- `message.list`：`sessionId`、`beforeSeq`、`limit`（默认 50，上限 200）；返回正序消息和 `nextBeforeSeq`。
- `run.get`、`agentRun.get`、`review.get`：读取运行、角色执行和审查详情。
- `artifact.get`：传 `artifactId`，返回元数据和 base64 文件内容。
- `provider.credential.set/delete`：传 `providerId`；设置额外传 `secret`，仅写入系统钥匙串。
- `settings.get/update`：读取／设置 `defaultWorkflowId`。
- `project.import`：传 `sourceKey` 和旧项目列表，按来源幂等导入。

新增 `stage.started/completed`、`artifact.created`、`review.completed` 事件。阶段事件包含 `agentRunId`、`stepKey`、`iteration`；`iteration` 从 0 开始。运行失败、取消和中断具有独立终态。

`run.resume` 继续使用 `sessionId`、`runId`、`interruptId`、`response`。流程级决策支持 `{action:"accept"|"cancel"|"retry"}`；达到返修上限后的 `retry` 表示用户明确追加一轮。无法审查后的 `retry` 只重新截图及审查，不重复编写。

## 历史和重启

公共上下文包含用户输入、澄清和最终结果；角色内部指令和交接不会无差别分享。每次执行保存历史消息引用及产物引用。默认上下文预算 8000 tokens，使用 UTF-8 字节上界进行保守估算，按完整公共轮次裁剪历史；过大的当前输入和交接明确报错。图片按实际内容提交，模型服务的图片计费和窗口限制仍以供应商为准。

`/clear` 和 `session.clear` 推进上下文边界，保留历史展示。CLI 可使用 `uv run aurora serve --session-id <UUID>` 继续同一会话。

重启后遗留活动运行、Agent 执行和工具任务变为 `interrupted`，待处理输入过期。不会恢复旧工具、旧审批或临时 Git 回滚。查看历史无需工作区在线；新一轮执行时才验证并重建工作区资源。

## 验证

```sh
uv run ruff check .
uv run pyright
uv run pytest
```

`src/agent/tests/test_persistence.py` 使用真实临时 SQLite 验证迁移、重启、历史隔离、分页、去重、配置快照和返修循环。`src/agent/tests/test_preview.py` 验证 URL／路径边界、进程取消和真实 Chromium 双视口截图；没有安装 Chromium 时仅真实浏览器测试跳过。协作模型测试使用可控模型响应，不调用收费模型服务。

## ORM 实体与工作单元

所有业务数据库操作使用 SQLAlchemy 2 ORM，实体集中在 `src/agent/src/aurora/agent/store/model/`。数据库仍是 SQLite，现有迁移及数据无需转换。依赖通过 `uv sync` 安装。

```python
from sqlalchemy import select
from aurora.agent.store.model import Agent

with database.transaction() as session:
    agent = session.scalars(select(Agent).where(Agent.name == "前端编写")).first()
    if agent is not None:
        agent.instructions = "根据需求编写页面并处理视觉审查问题"
```

事务内的属性修改由 Session 自动提交；异常时回滚。嵌套调用共享同一工作单元，模型或工具调用期间不得持有事务。实体外键关联可在 Session 内访问，例如 `agent.model_config.provider`。JSON 列维持现有文本存储格式，协议边界继续展开为对象，旧快照和历史可直接读取。

新增实体或字段时同时添加版本化 SQL 迁移并更新 ORM 模型。测试覆盖旧数据库读写、关系映射、约束一致性、回滚与并发消息序号。
