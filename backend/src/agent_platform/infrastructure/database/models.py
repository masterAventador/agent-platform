from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.auth import (
    AuthSessionRecord,
    UserRecord,
)
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.runs import RunEventRecord, RunRecord
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)

ALL_DATABASE_MODELS: tuple[type[Base], ...] = (
    UserRecord,
    AuthSessionRecord,
    TenantRecord,
    TenantMembershipRecord,
    EmployeeRecord,
    EmployeeVersionRecord,
    RunRecord,
    RunEventRecord,
)


def load_database_models() -> None:
    """显式注册全部 SQLAlchemy 模型到共享 Metadata。"""

    if not ALL_DATABASE_MODELS:
        raise RuntimeError("数据库模型注册表为空")
