<!--
提交前请确认：
- 已运行与改动层次对应的检查（见 CONTRIBUTING.md 的「提交前自检」）
- 若改动了层间边界或跨层契约，已同步更新 docs/architecture.md
-->

## 变更内容

## 涉及的层

<!-- 勾选所有被改动的层 -->
- [ ] `src/frontend` 前端（Nuxt）
- [ ] `src/tauri` Tauri 胶水层
- [ ] `src/rust` 纯 Rust crate
- [ ] `src/agent` python-agent
- [ ] `src/cli` python-cli
- [ ] 仓库级（CI / 文档 / 配置）

## 关联 Issue

## 测试

- [ ] Python：`uv run pytest`、`uv run ruff check .`、`uv run pyright`
- [ ] Rust：`cargo test --workspace`
- [ ] 前端：`pnpm lint && pnpm typecheck && pnpm test`
- [ ] 手动验证通过

## 截图（如适用）
