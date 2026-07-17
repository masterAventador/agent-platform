from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from capability_harness import CapabilityHarness, build_capability_harness

from agent_platform.config import AppSettings


@pytest_asyncio.fixture
async def capability_harness() -> AsyncIterator[CapabilityHarness]:
    harness, engine, client = await build_capability_harness(
        AppSettings(auth_cookie_secure=False)
    )
    async with client:
        yield harness
    await engine.dispose()


@pytest_asyncio.fixture
async def core_only_harness() -> AsyncIterator[CapabilityHarness]:
    harness, engine, client = await build_capability_harness(
        AppSettings(auth_cookie_secure=False, installed_capabilities=())
    )
    async with client:
        yield harness
    await engine.dispose()


@pytest_asyncio.fixture
async def video_harness() -> AsyncIterator[CapabilityHarness]:
    """Core+视频 交付 Profile 组合。"""

    harness, engine, client = await build_capability_harness(
        AppSettings(auth_cookie_secure=False, installed_capabilities=("video-studio",))
    )
    async with client:
        yield harness
    await engine.dispose()
