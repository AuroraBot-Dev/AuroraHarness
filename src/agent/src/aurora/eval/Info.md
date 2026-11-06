# 评估与校准

在仓库根运行 `uv run aurora eval`。14 个离线用例在独立临时工作区检查任务识别、强度分档、文件内容、审批与依赖阻断。确定性规划器面向基准输入，执行沿用实际任务图和文件工具。用例预期独立保存在 `fixtures.py`，增加用例无需改动执行图。

`uv run aurora eval --list` 列出用例，`--case ID` 可重复选择，`--json` 输出逐项期望和观测。退出码 0 表示全部通过，1 表示存在失败用例，2 表示输入或配置有误。

显式使用 `--live` 会请求配置的模型并消耗 token。内置任务为 README 总结和跨文件架构对比。比较时各档使用相同资料，评审从完整性、准确性与简洁性分别给 1–5 分。报告保留逐次结果，用量缺失显示未知。

```sh
uv run aurora eval --live --list
uv run aurora eval --live --task summarize_readme --tiers low,high --repeat 2
```

默认模型工厂读取 `AGENT_MODEL`、`AGENT_API_KEY`（或 `OPENAI_API_KEY`）以及可选的 `AGENT_BASE_URL`。SDK 的 `run_experiment` 可以分别注入工作模型和评审模型。`JsonlFeedbackStore` 另行保存用户反馈。
