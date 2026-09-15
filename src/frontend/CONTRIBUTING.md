# 参与贡献

## 开发环境

推荐将 `AuroraAgentFrontend/` 与 `AuroraAgentBackend/` 放在同一目录。运行 `pnpm setup` 安装两个仓库依赖，或设置 `AURORA_ROOT` 指向后端绝对路径。

## 分支与提交

分支使用 `feat/`、`fix/`、`docs/`、`refactor/` 或 `test/` 前缀。提交信息遵循 Conventional Commits，例如 `fix(sidecar): stop runtime process group`。

## 提交前检查

```bash
pnpm lint
pnpm typecheck
pnpm test
pnpm test:e2e
```

PR 应说明变更、关联 Issue、验证命令和界面截图。至少一名维护者 review 通过后合并。Bug Issue 需提供环境、复现步骤、实际结果与期望结果。
