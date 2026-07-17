class InvalidCurrentPassword(Exception):
    """修改密码时提供的当前密码不正确。"""


class TokenInvalidOrExpired(Exception):
    """账号一次性 token 无效、已消费或已过期。"""


class SessionNotFound(Exception):
    """目标会话不存在或不属于当前用户。"""
