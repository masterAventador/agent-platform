class RegistrationUnavailable(Exception):
    """当前注册信息不可用。"""


class InvalidCredentials(Exception):
    """登录凭据无效。"""


class AuthenticationRequired(Exception):
    """请求需要有效登录会话。"""


class RateLimitExceeded(Exception):
    """认证请求超过频率限制。"""
