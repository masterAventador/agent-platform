"""RedisAuthRateLimiter 的能力扩展限流作用域（L-1 STS 频控）。"""

from __future__ import annotations

import pytest

from agent_platform.infrastructure.security.rate_limits import RedisAuthRateLimiter
from agent_platform.platform.auth.errors import RateLimitExceeded


class FakeRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def expire(self, key: str, seconds: int) -> None:
        self.expirations[key] = seconds


@pytest.mark.asyncio
async def test_extra_scope_enforces_configured_limit_with_window_expiry() -> None:
    redis = FakeRedis()
    limiter = RedisAuthRateLimiter(
        redis,  # type: ignore[arg-type]
        register_limit=5,
        login_limit=5,
        extra_limits={"video_sts_issue": 2},
    )

    await limiter.ensure_allowed(scope="video_sts_issue", key="tenant-a")
    await limiter.ensure_allowed(scope="video_sts_issue", key="tenant-a")
    with pytest.raises(RateLimitExceeded):
        await limiter.ensure_allowed(scope="video_sts_issue", key="tenant-a")

    # 不同租户互不影响；窗口 60 秒过期。
    await limiter.ensure_allowed(scope="video_sts_issue", key="tenant-b")
    assert all(seconds == 60 for seconds in redis.expirations.values())


@pytest.mark.asyncio
async def test_auth_scopes_keep_existing_semantics() -> None:
    limiter = RedisAuthRateLimiter(
        FakeRedis(),  # type: ignore[arg-type]
        register_limit=1,
        login_limit=1,
        extra_limits={"video_sts_issue": 1},
    )
    await limiter.ensure_allowed(scope="register", key="user@example.com")
    with pytest.raises(RateLimitExceeded):
        await limiter.ensure_allowed(scope="register", key="user@example.com")
