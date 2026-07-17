class WorkflowNotFound(Exception):
    """租户下不存在该工作流。"""


class WorkflowVersionNotFound(Exception):
    """工作流下不存在该版本。"""


class WorkflowNameAlreadyExists(Exception):
    """同租户已存在同名工作流。"""


class WorkflowVersionAlreadyExists(Exception):
    """并发/重复写入撞上工作流版本号唯一约束。"""


class WorkflowNotPublished(Exception):
    """工作流尚未发布任何版本，不能被员工引用。"""
