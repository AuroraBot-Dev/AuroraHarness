# 存储层（Store）

负责保存和恢复 Agent 运行状态，包括数据库实体以及会话级工作区版本快照。

持久化实体：projects / sessions / messages / runs / tasks / attachments / approvals / 非敏感 settings / legacy import 记录。

启用 foreign keys、WAL 和版本化迁移；状态变更在事务内提交后才发事件（见 ADR-006、桌面应用架构）。

`git.py` 管理绑定工作区的 Git 状态、临时对象、运行前后快照和安全回滚。快照仅存在于会话生命周期内，不向项目仓库写入提交、分支或 stash。
