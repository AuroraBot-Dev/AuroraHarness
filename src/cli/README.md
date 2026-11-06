# aurora-cli（cli 层）

Aurora Agent 的命令行入口。这一层是**薄壳**：只做参数解析与命令分发，不承载业务逻辑。

## 职责边界

- 属于本层：`argparse` 解析、子命令注册、把参数翻译成对 `aurora-agent` 的调用、退出码。
- 不属于本层：Agent 运行时、图编排、工具实现、沙箱、存储、传输协议——那些都在 `src/agent`。

依赖方向是单向的：

```
src/cli (aurora-cli)  ──依赖──▶  src/agent (aurora-agent)
```

`aurora-agent` 不导入 `aurora.cli`，因此 agent 层可以脱离 CLI 独立安装与使用。

## 目录

```
src/cli/
├── pyproject.toml          # 分布定义：name = aurora-cli，[project.scripts] aurora = aurora.cli:main
├── src/aurora/cli/
│   ├── main.py             # 解析器构建与分发
│   ├── interactions.py     # serve/demo/sandbox 共用的中断应答辅助
│   └── commands/           # 每个子命令一个模块
└── tests/                  # 只放覆盖本层的测试
```

## 安装与运行

从仓库根目录（uv 工作区根）执行：

```bash
uv sync                       # 一次装好 aurora-agent 与 aurora-cli
uv run aurora --help
uv run aurora runtime --port 8765     # 浏览器开发用 WebSocket 传输
```

单独只装 CLI（会自动带上 agent）：

```bash
uv pip install ./src/cli
```

## 运行评估

`uv run aurora eval` 默认在临时工作区运行离线基准。用 `--list` 查看用例，`--case ID` 单独执行，`--json` 输出结构化报告。存在失败用例时命令返回非零退出码。

`uv run aurora eval --live --list` 列出两项文本对照任务。执行 `--live --task summarize_readme --tiers low,high` 时才会调用配置的模型并消耗 token。详见[评估与校准](../agent/src/aurora/eval/Info.md)。

## 测试

```bash
uv run pytest src/cli/tests -v
```
