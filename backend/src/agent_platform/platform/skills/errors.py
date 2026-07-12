class SkillNotFound(Exception):
    """当前租户中不存在指定 Skill。"""


class SkillVersionNotFound(Exception):
    """指定 Skill 版本不存在。"""


class SkillNameAlreadyExists(Exception):
    """当前租户中已存在同名 Skill。"""


class SkillNameMismatch(Exception):
    """新版本声明的名称与已有 Skill 不一致。"""
