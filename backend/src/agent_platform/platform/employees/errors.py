class EmployeeNotFound(Exception):
    """数字员工在当前租户不可见。"""


class EmployeeNameAlreadyExists(Exception):
    """当前租户已存在同名数字员工。"""


class EmployeeSkillNotBindable(Exception):
    """数字员工只能绑定当前租户已发布的 Skill。"""


class EmployeeToolNotBindable(Exception):
    """数字员工只能绑定当前租户中已启用 Server 的已启用 Tool。"""
