# 文档索引

`docs/` 下的内容分两类，读之前先分清，免得照着过时的约定操作。

## 现行文档

| 文档 | 内容 |
|---|---|
| [`architecture.md`](architecture.md) | 分层结构、层间契约、平台差异约定、CI 覆盖范围、已知偏离与待办 |
| [`adr/`](adr/README.md) | 架构决策记录：协议版本集中管理、不做 mock 回退、不引入 langchain 元包 |
| [`database.md`](database.md) | SQLite 持久化：数据结构、迁移、重启行为、视觉审查流程 |
| [`agent-registry.md`](agent-registry.md) | Agent 注册、动态委派与审批恢复 |

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
