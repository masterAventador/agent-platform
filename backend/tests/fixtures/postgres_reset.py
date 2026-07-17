"""真实 PostgreSQL 集成测试的共享库清理（T9）。

各测试文件此前各自维护一份「手工删表清单」，按人肉排的顺序 ``delete()`` 一遍。
这种做法结构性不可维护：任何人新增一张带 ``users`` / ``tenants`` 外键的表，都会
在某个不相关的测试文件里炸出 FK 错误，且报错位置离根因很远。

这里改为从 ``Base.metadata`` 自动推导表清单，用单条
``TRUNCATE ... RESTART IDENTITY CASCADE`` 清空。相比反序 ``DELETE``：

* **顺序无关**：TRUNCATE 一次性处理全部表，不需要有人维护正确的删除顺序；
* **对未登记的表免疫**：``Base.metadata`` 的内容取决于哪些模块被 import 过
  （能力包 ``video_studio`` 的表只有在其模块被导入后才登记）。反序 DELETE 在
  这些表缺席时会直接 FK 失败，而 CASCADE 会沿真实外键图连带清空它们——
  这正是本模块要根治的那类缺陷；
* **不碰簿记表**：``alembic_version``、``employee_model_migration_backups`` 和
  LangGraph 的 ``checkpoint_*`` 都没有指向业务表的外键，CASCADE 不会波及，
  迁移状态因而在测试文件之间保持完好。

新增业务表时无需修改本文件。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models


async def reset_database(engine: AsyncEngine) -> None:
    """清空 ``Base.metadata`` 登记的全部业务表，保留迁移簿记表。

    只支持 PostgreSQL：本清理依赖 ``TRUNCATE ... CASCADE`` 的外键连带语义，
    在其他方言上没有等价行为，因此显式失败而不是静默降级成不完整的清理。
    """

    if engine.dialect.name != "postgresql":
        raise RuntimeError(
            f"reset_database 只支持 PostgreSQL，收到方言 {engine.dialect.name!r}；"
            "内存 SQLite 用例应改用一次性 engine 而不是共享库清理。"
        )

    load_database_models()
    preparer = engine.dialect.identifier_preparer
    tables = [preparer.format_table(table) for table in Base.metadata.sorted_tables]
    if not tables:
        raise RuntimeError("Base.metadata 未登记任何表，清理范围为空说明模型注册被破坏")

    async with engine.begin() as connection:
        await connection.execute(
            text(f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE")
        )
