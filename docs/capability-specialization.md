# 能力特化与本轮能力清单

能力特化把项目背景、附加指令、工具与检索结果注入现有角色，不改变委派图结构。实现位于 `src/agent`，通过 `aurora.agent.capability` 导出公共 Python 接口；不会加载工作区里的任意脚本。

设计来源：[文档站的能力特化层](https://haha-ha-cuo.github.io/AuroraAgent-demo/architecture/capability-layer)。缓存仍是独立的内部优化。

## 注册策略

```python
from pathlib import Path

from aurora.agent.capability import (
    Capability,
    CapabilityPolicy,
    CapabilityRegistry,
    CapabilityScope,
)
from aurora.agent.orchestration.runtime import AgentRuntime

capabilities = CapabilityRegistry()
capabilities.register(
    CapabilityPolicy(
        name="team-conventions",
        apply=lambda context: Capability(
            context="该项目使用 uv 管理 Python 环境。",
            instructions="回答使用中文，报告实际执行过的验证。",
        ),
        scope=CapabilityScope("project", Path("/path/to/project")),
        priority=10,
    )
)
runtime = AgentRuntime(capabilities=capabilities)
```

`Capability` 提供四类注入：

| 字段 | 含义 |
| --- | --- |
| `context` | 作为当前输入中的参考材料传递，不提升为系统指令 |
| `instructions` | 追加到角色指令，保留角色原有要求 |
| `tools` | `Tool` 元组；与角色工具白名单取交集后交给原确认门 |
| `retrieve` | 可选的同步检索回调，接收 `CapabilityContext` 并返回参考文本 |

策略回调属于应用宿主注册的可信 Python 扩展。注册本身不授权工具执行；自定义工具应使用会话沙箱实现文件或命令操作。检索回调只应读取所需信息，不执行需审批的操作。普通用户创建角色和提示词仍使用既有界面。

## 作用域与组合规则

| 作用域 | 匹配规则 |
| --- | --- |
| `global` | 所有项目与角色 |
| `project` | 规范化后的项目目录完全相同 |
| `subtree` | 项目相同，任务路径以给定路径段开头 |
| `task` | 项目相同，任务路径完全相同 |

`CapabilityContext` 包含 `project_path`、`goal`、`agent_id` 和 `task_path`。项目路径通过 `Path.resolve()` 规范化；不按字符串前缀匹配项目。任务路径是任务 ID 元组，不是用斜杠拼接的字符串。

主角色的任务路径为 `()`，每次委派在父路径后追加规划任务 ID；例如 `(根委派ID, 子委派ID)`。这里的单任务指一次交给 Agent 的任务，工具叶子不会单独建立模型上下文。递归角色按完整路径解析能力，仍受本轮冻结的角色白名单与结构上限约束。回调可用 `agent_id` 进一步选择特定角色。

策略按 `(priority, 作用域层级, 路径深度, name)` 升序应用。优先级相同时，全局先于项目，项目先于子树和单任务；同层按名称排序，与注册先后无关。文本按顺序追加；多个策略提供同名自定义工具时，后者生效。内置工具、MCP 工具及 `list_agents`、`call_agent`、`agent_respond` 不允许被策略覆盖。

每项策略先完成回调、检索、内容及工具校验，再整体合并。任一步异常、重名冲突或内容超限时跳过整项，保留默认能力和其他成功策略。日志只记录失败策略名，不记录异常原文或检索内容。

解析器的附加文本预算默认 16,000 个 UTF-8 字节；运行时还会以当前模型剩余预算收紧它。过大的策略回退，不挤掉当前用户输入。历史仍按完整公共轮次裁剪。

## 本轮能力清单

新设计让每个角色的能力组合可以解释和复核：

1. 运行开始时冻结注册清单，之后注册或移除策略只影响下一轮。
2. 角色开始时解析匹配策略，把实际参考材料写入 `AgentRun.input.current`，把最终指令写入角色配置快照。
3. `AgentRun.input.capabilities.applied` 按应用顺序保存成功策略名，`failed` 保存回退策略名，可通过 `run.get` 或 `agentRun.get` 读取。
4. 审批恢复复用该角色已构建的图、工具和输入，不重新调用策略或检索器，不重放已完成任务。

冻结的是注册项，不会复制回调依赖的外部文件、网络索引或可变闭包。后续阶段首次解析时仍可能看到更新后的外部资料；历史记录保存的是当次实际输入。需要严格固定版本的策略，应在注册时绑定不可变资料或版本化索引。

上下文特化已接入编写、直接对话、仅规划、动态委派、视觉审查和汇总阶段。审查与汇总始终不执行注入工具。清单目前通过运行详情接口读取，专门的界面视图、声明式配方导入及云端同步留待后续实现。

## 验证

在仓库根执行：

```bash
node scripts/tasks.mjs lint
node scripts/tasks.mjs test-python
```

`test_specialization.py` 覆盖作用域隔离、确定性排序、检索失败后的原子回退、工具白名单、审批恢复、委派继承、内容预算和持久化能力清单。
