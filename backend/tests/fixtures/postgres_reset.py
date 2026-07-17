"""真实 PostgreSQL 集成测试的共享库清理（T9）。

各测试文件此前各自维护一份「手工删表清单」，按人肉排的顺序 ``delete()`` 一遍。
这种做法结构性不可维护：任何人新增一张带 ``users`` / ``tenants`` 外键的表，都会
在某个不相关的测试文件里炸出 FK 错误，且报错位置离根因很远。

这里改为从 ``Base.metadata`` 自动推导表清单，用单条
``TRUNCATE ... RESTART IDENTITY CASCADE`` 清空。相比反序 ``DELETE``：

* **顺序无关**：TRUNCATE 一次性处理全部表，不需要有人维护正确的删除顺序；
* **覆盖已登记的孤岛表**：``run_dead_letters`` / ``tool_audit_events`` 的
  tenant_id、user_id 是裸 uuid 列、不建外键约束，靠 ``delete(users)`` 的 CASCADE
  永远够不到；它们在 metadata 里，因此被直接列进 TRUNCATE；
* **对「未登记但有外键路径」的表连带清空**：``Base.metadata`` 的内容取决于哪些
  模块被 import 过（能力包 ``video_studio`` 的表只有在其模块被导入后才登记）。
  反序 DELETE 在这些表缺席时会直接 FK 失败，而 CASCADE 会沿真实外键图连带清空。

**这项保证的边界（不要读成「对未登记的表免疫」）**：CASCADE 只沿真实外键走。
若将来出现**既不在 metadata、又没有外键约束**的表（例如某能力包只用裸
``tenant_id`` 列、不建约束），它既躲过 TRUNCATE 列表、也躲过 CASCADE，会被静默
漏清。这类表必须登记进 ``Base.metadata``（或由该能力包自己的夹具清理），本模块
无法自动发现它们。

不碰的簿记表：``alembic_version`` 和 ``employee_model_migration_backups`` 都不在
``Base.metadata``、也没有指向业务表的外键，因此 TRUNCATE 列表和 CASCADE 都够不到，
迁移状态在测试文件之间保持完好。

**作用范围（不要读成「根治跨文件污染」）**：21 个用 ``TEST_DATABASE_URL`` 的测试
文件里只有 4 个调用本模块，其余仍会把数据留在共享库里（它们自身的断言按 uuid /
租户收窄，所以不红）。本模块让**调用方**对前序残留免疫，并不阻止残留产生。

新增业务表时无需修改本文件。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from agent_platform.bootstrap.demo_seed import DEMO_EMAIL
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.models import load_database_models

# TRUNCATE 需要 ACCESS EXCLUSIVE。拿不到就等于有连接泄漏了事务，等下去也不会好，
# 快失败并指出泄漏点远比无声挂死有用。
_LOCK_TIMEOUT = "5s"

# PostgreSQL: lock_not_available
_LOCK_NOT_AVAILABLE_SQLSTATE = "55P03"


class DatabaseResetLockTimeout(RuntimeError):
    """TRUNCATE 在 ``lock_timeout`` 内拿不到 ACCESS EXCLUSIVE 锁。"""


class DatabaseResetRefused(RuntimeError):
    """目标库不像可丢弃的测试库，拒绝清空。"""


def _sqlstate_of(error: DBAPIError) -> str | None:
    original = error.orig
    for attribute in ("sqlstate", "pgcode"):
        code = getattr(original, attribute, None)
        if isinstance(code, str):
            return code
    return None


async def _refuse_if_database_holds_demo_seed(engine: AsyncEngine) -> None:
    """护栏：库里有人工维护的 Demo Seed 就拒绝清空。

    库名/端口都不能用作判据——常驻开发栈 ``agent-platform-dev`` 与
    ``infra/platform/test-mvp-profile.sh:440`` 的一次性验收栈都叫 ``agent_platform``、
    都在 127.0.0.1、端口随机。可靠的区别在语义：Demo Seed 是给人工验收用的、
    重建代价由用户承担的数据，任何测试都不会创建它。
    """

    async with engine.connect() as connection:
        if await connection.scalar(text("SELECT to_regclass('public.users')")) is None:
            return  # 尚未迁移的空库，没有可保护的数据
        seeded = await connection.scalar(
            text("SELECT 1 FROM users WHERE email = :email LIMIT 1"), {"email": DEMO_EMAIL}
        )
    if seeded is not None:
        raise DatabaseResetRefused(
            f"目标库存在 Demo Seed 账号 {DEMO_EMAIL}，拒绝执行 TRUNCATE。"
            "这通常意味着 TEST_DATABASE_URL 指向了常驻开发栈 agent-platform-dev "
            "或已 seed 的验收栈；清空会毁掉用户手工验收所依赖的数据。"
            "真实 PG 门禁请指向一次性测试库。"
        )


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

    await _refuse_if_database_holds_demo_seed(engine)

    load_database_models()
    preparer = engine.dialect.identifier_preparer
    tables = [preparer.format_table(table) for table in Base.metadata.sorted_tables]
    if not tables:
        raise RuntimeError("Base.metadata 未登记任何表，清理范围为空说明模型注册被破坏")

    statement = f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE"
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f"SET LOCAL lock_timeout = '{_LOCK_TIMEOUT}'"))
            await connection.execute(text(statement))
    except DBAPIError as error:
        if _sqlstate_of(error) != _LOCK_NOT_AVAILABLE_SQLSTATE:
            raise
        raise DatabaseResetLockTimeout(
            f"TRUNCATE 在 {_LOCK_TIMEOUT} 内拿不到 ACCESS EXCLUSIVE lock，已放弃。"
            "通常是某个用例泄漏了未关闭的 session/事务仍持有这些表的锁；"
            "请检查最近一次用例是否有未 close 的 AsyncSession 或未结束的 engine。"
        ) from error
