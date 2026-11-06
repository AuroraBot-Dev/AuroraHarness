# 本地数据库与多 Agent 协作

Aurora 使用 Python 后端管理 SQLite，桌面端与浏览器端通过协议版本 1 访问。每个会话选择一个预设流程，每轮运行冻结流程、Agent、模型、工具权限和预览配置。多个角色在同一个工作区串行执行。

## 启动和配置

```sh
uv sync
uv run playwright install chromium
uv run aurora runtime --port 8765
```

数据库默认保存在仓库根的 `.aurora/aurora.db`，浏览器与桌面开发共用。独立安装或打包运行时若无法定位源码仓库，则使用当前工作目录下的 `.aurora/aurora.db`。通过 `AURORA_DATABASE_PATH` 指定其他位置。一个数据库同时只允许一个运行时持有；桌面端和独立 CLI 不应同时接管同一个数据库。

初次启动从环境变量导入默认配置，未设置模型时保存 `unconfigured`，仍可浏览配置和历史，但执行前必须配置有效模型。数据库已有配置时不会被 `.env` 覆盖。

在前端设置页依次添加供应商、模型、Agent 和协作流程：

1. 供应商设置 OpenAI 兼容端点。凭据可使用 `env:VARIABLE_NAME`；填写 API Key 时通过系统钥匙串保存，数据库记录 `keyring:供应商UUID`。
2. 模型记录服务端模型名称、参数及输入能力。视觉审查角色必须使用声明支持图片且服务端确实支持图片的模型。
3. Agent 分别设置职责与工具白名单；`*` 表示全部，空列表表示不允许调用工具。审查与汇总阶段始终不执行工具。
4. 流程选择单 Agent 或前端视觉审查，绑定编写与审查 Agent。默认最多返修两轮。
5. 会话顶部可切换下一轮使用的流程。首次需要截图时，通过表单填写本机预览 URL、启动命令、工作区内目录和页面。

预览命令经过审批并使用会话沙箱；截图使用独立 Chromium 上下文，默认视口 1440×900、390×844。命令留空时连接已经运行的服务。预览命令设置 90 秒生命周期上限，截图结束立即取消并清理所启动的进程。缺少浏览器、启动失败、截图失败或图片能力不匹配都会记录 `unable_to_review`，不会当作通过。

## 实体和约束

完整结构由 `src/agent/src/aurora/agent/store/migrations/` 中的版本化 SQL 定义。`001` 为初始结构，`002` 增加 Agent 注册及委派，`003` 增加审批期限和解决元数据，`004` 增加运行与任务重试来源。

- `projects`、`sessions`、`messages` 管理工作区及对话；消息按会话内序号排序，分为公共消息和角色内部交接。
- `model_providers` → `model_configs` → `agents` 管理连接与角色；`workflows`、`workflow_steps` 固定阶段与角色绑定。
- `runs` 表示一次用户请求；`agent_runs` 表示特定角色在特定阶段、轮次的执行；`tasks` 表示该执行中的工具任务。
- `artifacts` 保存截图、代码差异及预览日志的路径、哈希和来源；文件在数据库旁的 `artifacts/运行UUID/` 中。产物读取验证路径归属和 SHA-256。
- `reviews`、`review_artifacts`、`review_findings` 记录审查结论、证据及问题，不覆盖旧轮次。问题只可引用本轮截图，历史问题关联只可指向同一运行。
- `interactions` 保存审批、澄清、评价和返修决策；`run_events` 保存有序阶段轨迹；其余表用于设置、迁移和旧数据导入。

危险操作审批额外保存 `expires_at`、`resolved_at`、`resolution`；批准/拒绝为 resolved，超时、取消及重启失效为 expired，并保存明确的布尔拒绝结果。截止时间与解决事件持久化，更新后的页面从数据库恢复展示，不能将过期记录再次提交。旧版历史响应保留，缺失的历史元数据不伪造。

重试运行通过 `retry_of_run_id` 关联来源运行，局部重试额外保存 `retry_task_id`，新任务的 `retry_of_task_id` 关联原任务。来源记录不可被新运行改写；外键的 SET NULL 删除语义允许完整会话级联清理。角色输入及配置快照保存本轮实际权限、重试作用域与保留结果引用；图片复制后属于新运行，同时保留原产物来源。

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
- `run.retry`：`session_id`、`run_id`、可选 `task_id`；新建来源关联运行并立即返回 `{run}`，后续执行使用相同事件通道。整轮重试跳过已完成任务，局部重试只覆盖指定范围；详见[运行与任务重试](architecture.md#运行与任务重试)。
- `approval.resolve`：`session_id`、`run_id`、`approval_id`、布尔 `approved`；接受后立即返回 `{accepted:true}`，通过 `approval.resolved` 确认批准、拒绝或超时。重复、未知和过期审批返回明确错误。默认期限 300 秒，详见[审批期限与确认](architecture.md#审批期限与确认)。
- `message.list`：`sessionId`、`beforeSeq`、`limit`（默认 50，上限 200）；返回正序消息和 `nextBeforeSeq`。
- `run.get`、`agentRun.get`、`review.get`：读取运行、角色执行和审查详情。
- `artifact.get`：传 `artifactId`，返回元数据和 base64 文件内容。
- `provider.credential.set/delete`：传 `providerId`；设置额外传 `secret`，仅写入系统钥匙串。
- `settings.get/update`：读取／设置 `defaultWorkflowId`。
- `project.import`：传 `sourceKey` 和旧项目列表，按来源幂等导入。

新增 `stage.started/completed`、`artifact.created`、`review.completed` 事件。阶段事件包含 `agentRunId`、`stepKey`、`iteration`；`iteration` 从 0 开始。运行失败、取消和中断具有独立终态。

`run.resume` 继续使用 `sessionId`、`runId`、`interruptId`、`response`。流程级决策支持 `{action:"accept"|"cancel"|"retry"}`；达到返修上限后的 `retry` 表示用户明确追加一轮。无法审查后的 `retry` 只重新截图及审查，不重复编写。

## 任务依赖

迁移 007 为任务保存同角色计划中的前置键和直接阻断来源。未声明依赖的旧记录使用 NULL，声明独立任务使用空数组。会话、运行、角色详情与实时事件均用同一批任务 UUID 返回 `dependsOn` 和 `blockedBy`。依赖未通过的任务记录为 `blocked`，没有审批或工具调用。

## 工具调用轨迹

迁移 `005_tool_calls.sql` 增加 `tool_calls`。每次通过确认门后的实际叶子工具调用有独立 UUID，关联运行、角色执行和规划任务键；`run.get` 返回 `toolCalls` 并关联持久任务 ID。委派本身由角色与任务树表示，不伪造成一次叶子调用。

事件为 `tool.started/completed/failed/cancelled/interrupted`，由运行时通知通道广播，并写入审计序列。事件使用 `tool_call_id` 定位，记录结构化输入、文本输出/错误、UTC 起止时间和单调时钟测得的 `duration_ms`。等待或拒绝审批不创建记录；工具非零退出和超时均记录失败，取消记录独立终态。重试产生新运行及新调用，不修改来源轨迹。

前端按调用 ID 合并快照与事件，迟到的执行中快照不能覆盖终态。结构化敏感字段和已配置供应商凭据在入库及推送前脱敏；这不代表所有任意文本及外部工具的凭据安全边界已完成，B10 的验收仍未结束。

运行时重启将遗留的执行中调用标记中断，恢复时间仅用于标记已结束，`duration_ms` 保持未知，不能把进程停机时长算成工具耗时。调用记录随运行和会话级联删除。

## 历史和重启

公共上下文包含用户输入、澄清和最终结果；角色内部指令和交接不会无差别分享。每次执行保存历史消息引用及产物引用。默认上下文预算 8000 tokens，使用 UTF-8 字节上界进行保守估算，按完整公共轮次裁剪历史；过大的当前输入和交接明确报错。图片按实际内容提交，模型服务的图片计费和窗口限制仍以供应商为准。

`/clear` 和 `session.clear` 推进上下文边界，保留历史展示。CLI 可使用 `uv run aurora serve --session-id <UUID>` 继续同一会话。

迁移 006 的 `run_checkpoints` 在等待人工输入时记录当前执行位置，和待处理输入在同一事务中提交。SQLite 保存图状态、已完成任务的结果、结构预算和 Git 包摘要。Git 基线存放在工作树外的运行专用目录，恢复或中断后随运行收束清理。

启动时核对 Git 可见的工作树、索引和分支，以及角色权限、配置和待处理输入。Git 忽略的文件和外部服务的状态超出这项核对范围。校验通过的运行保留原 ID，通过 `session.get` 读取换发的输入标识后继续。审批沿用原截止时间，过期会拒绝操作。准备继续执行时还会在工作区锁内复核。执行工具时崩溃、工作区发生变化、检查点损坏或缺失时，遗留运行标为 `interrupted`，可从历史发起重试。

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
