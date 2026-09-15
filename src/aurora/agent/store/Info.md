# 存储层（Store）

`model/` 使用 SQLAlchemy 2 声明式 ORM 对所有业务表建模，定义字段、主外键、关联、检查约束与索引。`Base` 是实体基类，`entities.py` 包含项目、供应商、模型配置、Agent、流程、会话、运行、消息、工具任务、产物、审查、交互及迁移记录。

`database.py` 管理 SQLite 连接、外键、WAL、版本化迁移和 ORM 工作单元。`transaction()` 返回 SQLAlchemy `Session`，同一线程内嵌套调用共享 Session；最外层负责提交或回滚，连接锁确保并发线程不会串用事务。实体通过 `Session.add/get/delete` 或模型表达式读写，普通属性修改由 Session 跟踪。

`records.py` 管理业务校验、配置快照、历史上下文和产物文件。对外协议仍返回字典；实体在 Session 内通过 `to_record()` 转换，不向调用者泄漏延迟加载对象。

`ownership.py` 保证同一数据库只由一个运行时接管，避免重启恢复误伤活动任务。

`migrations/` 保留已有版本化 SQL，打开旧数据库不会重建表。结构变更需要同时维护模型与新增迁移；禁止使用 `create_all()` 替代正式迁移。SQLite 原始接口只用于迁移、连接配置和诊断，业务访问统一使用 ORM。

`git.py` 管理会话工作区快照及回滚，快照生命周期仍限于当前运行时资源，不写入项目提交、分支或 stash。
