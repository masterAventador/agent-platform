class SkillNotFound(Exception):
    """当前租户中不存在指定 Skill。"""


class SkillVersionNotFound(Exception):
    """指定 Skill 版本不存在。"""


class SkillNameAlreadyExists(Exception):
    """当前租户中已存在同名 Skill。"""


class SkillNameMismatch(Exception):
    """新版本声明的名称与已有 Skill 不一致。"""


class SkillReviewBlocked(Exception):
    """Skill 版本安全审核未通过，不能发布。"""


class SkillAlreadyDeleted(Exception):
    """Skill 已删除。"""


class SkillInUse(Exception):
    """Skill 仍被员工草稿或已发布员工版本引用。"""
