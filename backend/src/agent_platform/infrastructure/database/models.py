from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.repositories.account_tokens import (
    AccountTokenRecord,
)
from agent_platform.infrastructure.database.repositories.approvals import ApprovalRecord
from agent_platform.infrastructure.database.repositories.artifacts import (
    ArtifactRecord,
    ArtifactStorageOperationRecord,
    FileRecord,
    TaskAttachmentRecord,
)
from agent_platform.infrastructure.database.repositories.audit import (
    AuditChainStateRecord,
    AuditEventRecord,
    ToolAuditRecord,
)
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
from agent_platform.infrastructure.database.repositories.entitlements import (
    CapabilityEntitlementRecord,
)
from agent_platform.infrastructure.database.repositories.invitations import (
    TenantInvitationRecord,
)
from agent_platform.infrastructure.database.repositories.knowledge import KnowledgeBaseRecord
from agent_platform.infrastructure.database.repositories.memories import MemoryRecord
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
    MemoryRecord,
    SkillRecord,
    SkillVersionRecord,
    McpServerRecord,
    ToolRecord,
    SandboxLeaseRecord,
    AuditChainStateRecord,
    AuditEventRecord,
    ToolAuditRecord,
    TenantModelGatewayPolicyRecord,
    ModelGatewayProvisioningCommandRecord,
    FileRecord,
    TaskAttachmentRecord,
    ArtifactRecord,
    ArtifactStorageOperationRecord,
    CapabilityEntitlementRecord,
    ApprovalRecord,
    TenantInvitationRecord,
    AccountTokenRecord,
)


def load_database_models() -> None:
    """显式注册全部 SQLAlchemy 模型到共享 Metadata。"""

    if not ALL_DATABASE_MODELS:
        raise RuntimeError("数据库模型注册表为空")
