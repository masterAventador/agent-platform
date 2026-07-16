from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.artifacts import (
    ArtifactRecord,
    ArtifactStorageOperationRecord,
    FileRecord,
    TaskAttachmentRecord,
)
from agent_platform.infrastructure.database.repositories.audit import ToolAuditRecord
from agent_platform.infrastructure.database.repositories.auth import (
    AuthSessionRecord,
    UserRecord,
)
from agent_platform.infrastructure.database.repositories.conversations import (
    ConversationMessageRecord,
    ConversationRecord,
)
from agent_platform.infrastructure.database.repositories.dead_letters import RunDeadLetterRecord
from agent_platform.infrastructure.database.repositories.employees import (
    EmployeeRecord,
    EmployeeVersionRecord,
)
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.infrastructure.database.repositories.model_gateway import (
    ModelGatewayProvisioningCommandRecord,
    TenantModelGatewayPolicyRecord,
)
from agent_platform.infrastructure.database.repositories.runs import (
    RunCommandRecord,
    RunEventRecord,
    RunRecord,
)
from agent_platform.infrastructure.database.repositories.runtime_ownership import (
    RuntimeOwnershipRecord,
)
from agent_platform.infrastructure.database.repositories.sandbox import SandboxLeaseRecord
from agent_platform.infrastructure.database.repositories.skills import (
    SkillRecord,
    SkillVersionRecord,
)
from agent_platform.infrastructure.database.repositories.tenants import (
    TenantMembershipRecord,
    TenantRecord,
)
from agent_platform.infrastructure.database.repositories.tools import McpServerRecord, ToolRecord

ALL_DATABASE_MODELS: tuple[type[Base], ...] = (
    UserRecord,
    AuthSessionRecord,
    TenantRecord,
    TenantMembershipRecord,
    EmployeeRecord,
    EmployeeVersionRecord,
    ConversationRecord,
    ConversationMessageRecord,
    RunRecord,
    RunEventRecord,
    RunCommandRecord,
    RuntimeOwnershipRecord,
    RunDeadLetterRecord,
    KnowledgeBaseRecord,
    SkillRecord,
    SkillVersionRecord,
    McpServerRecord,
    ToolRecord,
    SandboxLeaseRecord,
    ToolAuditRecord,
    TenantModelGatewayPolicyRecord,
    ModelGatewayProvisioningCommandRecord,
    FileRecord,
    TaskAttachmentRecord,
    ArtifactRecord,
    ArtifactStorageOperationRecord,
)


MIGRATION_INTERNAL_TABLE_NAMES: frozenset[str] = frozenset(
    {
        # 20260714_0016 为支持 downgrade 还原而长期保留的迁移簿记表，
        # 不对应任何 ORM 模型，autogenerate 必须忽略它。
        "employee_model_migration_backups",
    }
)


def include_name_for_autogenerate(
    name: str | None,
    type_: str,
    parent_names: object,
) -> bool:
    """Alembic autogenerate 的 ``include_name`` 钩子：忽略迁移内部簿记表。"""

    del parent_names
    if type_ == "table":
        return name not in MIGRATION_INTERNAL_TABLE_NAMES
    return True


def load_database_models() -> None:
    """显式注册全部 SQLAlchemy 模型到共享 Metadata。"""

    if not ALL_DATABASE_MODELS:
        raise RuntimeError("数据库模型注册表为空")
