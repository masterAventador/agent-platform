import hashlib
from collections.abc import Mapping

from redis.asyncio import Redis

from agent_platform.platform.auth.errors import RateLimitExceeded


class RedisAuthRateLimiter:
    def __init__(
        self,
        redis: Redis,
        *,
        register_limit: int,
        login_limit: int,
        password_reset_limit: int | None = None,
        extra_limits: Mapping[str, int] | None = None,
    ) -> None:
        self._redis = redis
        # 找回密码限流缺省回退到登录限流额度，保证既有调用方无需改造即可注册该 scope。
        reset_limit = login_limit if password_reset_limit is None else password_reset_limit
        self._limits = {
            "register": register_limit,
            "register_ip": register_limit,
            "login": login_limit,
            "login_ip": login_limit,
            "password_reset": reset_limit,
            "password_reset_ip": reset_limit,
            # 能力包扩展限流作用域（如 video_sts_issue），由组合根按部署配置注入。
            **dict(extra_limits or {}),
        }

    async def ensure_allowed(self, *, scope: str, key: str) -> None:
        key_digest = hashlib.sha256(key.encode()).hexdigest()
        redis_key = f"auth-rate:{scope}:{key_digest}"
        request_count = await self._redis.incr(redis_key)
        if request_count == 1:
            await self._redis.expire(redis_key, 60)
        if request_count > self._limits[scope]:
            raise RateLimitExceeded
