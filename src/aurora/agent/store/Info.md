# 存储层（Store）

`database.py` 管理 SQLite 连接、外键、WAL 和版本化事务迁移。
`records.py` 管理项目、会话、角色与模型配置、执行记录、交接消息、审查证据及产物文件。
`ownership.py` 保证同一数据库只由一个运行时接管，避免重启恢复误伤活动任务。

`migrations/` 中的 SQL 是结构来源；字段与协议说明见仓库 `docs/database.md`。

`git.py` 管理会话工作区快照及回滚，快照生命周期仍限于当前运行时资源，不写入项目提交、分支或 stash。
