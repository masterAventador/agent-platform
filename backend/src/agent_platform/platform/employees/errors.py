class EmployeeNotFound(Exception):
    """数字员工在当前租户不可见。"""


class EmployeeNameAlreadyExists(Exception):
    """当前租户已存在同名数字员工。"""
