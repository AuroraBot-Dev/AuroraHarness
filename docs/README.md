# 文档索引

开发版已支持递归委派、任务依赖、审批与局部重试。等待人工输入时可保存检查点，重启后继续原运行。会话里的「调用轨迹」可以查看每次工具执行的参数、结果和耗时。[打开文档站](https://haha-ha-cuo.github.io/AuroraAgent-demo/)

从源码开始使用请看[快速开始](../README.md#快速开始)。维护者可以在仓库根执行 `uv run aurora eval`，用 14 个离线用例检查规划、工具结果和审批行为。运行资源总预算、持续规划、附件和跨平台桌面发布的进度见[需求与验收清单](requirements-status.md)。

## 使用与定制

| 目标 | 入口 |
| --- | --- |
| 自定义 Agent、提示词、模型、协作方式与配色 | [定义你的工作方式](customization.md) |
| 理解界面结构、响应式和代码组织 | [界面与自定义体验](interface-design.md) |
| 切换对话与真实调用轨迹 | [会话运行画布](customization.md#在对话与调用轨迹间切换) |
| 运行离线评估或比较文本任务的模型档位 | [评估与校准](../src/agent/src/aurora/eval/Info.md) |
| 安装、开发与构建 | [仓库 README](../README.md) |

## 现行文档

| 文档 | 内容 |
|---|---|
| [`customization.md`](customization.md) | 用户定制指南：Agent、提示词变量、导入导出、十六进制配色与数据位置 |
| [`interface-design.md`](interface-design.md) | 界面设计规则、图标与字体、响应式、动态效果与格式化约定 |
| [`architecture.md`](architecture.md) | 分层结构、层间契约、平台差异约定、CI 覆盖范围、已知偏离与待办 |
| [`adr/`](adr/README.md) | 架构决策记录：协议版本集中管理、不做 mock 回退、不引入 langchain 元包 |
| [`database.md`](database.md) | SQLite 持久化：数据结构、迁移、重启行为、视觉审查流程 |
| [`agent-registry.md`](agent-registry.md) | Agent 注册、动态委派与审批恢复 |
| [`capability-specialization.md`](capability-specialization.md) | 可插拔能力策略、作用域、优先级、回退与本轮能力清单 |
| [`python-modules.md`](python-modules.md) | 全仓 Python 模块职责、命名规则与重命名验证边界 |

其余入口：

- 分层总览、快速开始、任务入口 → 仓库根 [`README.md`](../README.md)
- 协作规范（分支、提交、钩子、自检） → [`CONTRIBUTING.md`](../CONTRIBUTING.md)
- 各层的实现说明 → `src/frontend/README.md`、`src/tauri/README.md`、`src/rust/README.md`、
  `src/agent/README.md`、`src/cli/README.md`

## 历史文档

[`notes/`](notes/) 记录的是**合并前双仓库时期**（`AuroraAgentBackend` + `AuroraAgentFrontend`）
的工程化过程。其中的目录约定、命令与工具选型多数已经过时——例如它们还在描述 Makefile、
`AURORA_ROOT` 指向同级后端仓库、`src-tauri/` 目录等，这些在当前仓库都不成立。保留是为了留下
决策过程与踩坑记录：

| 文档 | 内容 |
|---|---|
| [`notes/踩坑记录.md`](notes/踩坑记录.md) | 当时遇到并修掉的 8 个问题。其中跨平台相关的教训已固化为 [`architecture.md`](architecture.md) 的「平台差异」一节 |
| [`notes/规范化建议文档.md`](notes/规范化建议文档.md) | 当时的工程化审计与优先级建议清单 |

两份文档开头都有「历史文档」抬头。**不要按它们里面的命令操作**；现行做法以根 README 与
`architecture.md` 为准。
