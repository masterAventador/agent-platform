"""素材库生产回收清扫（M-2）。

参考 C04 未绑定文件 TTL 清扫与 C14 审计保留清扫的既有模式：API lifespan
常驻任务、配置驱动间隔、逐素材独立事务、失败受控日志并保留标记下轮重试。

误删边界（唯一允许的两类动作）：
- 超过 ``upload_expires_at`` 的 ``pending_upload`` 草稿 → ``upload_failed`` + 待清理；
- ``cleanup_required=True`` 的素材 → 删除存储对象后清除标记。
清理器未配置（无真实 COS 凭据）时仍执行过期止血，对象回收留待配置后补收。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_platform.capabilities.video_studio.media_library import (
    MediaLibraryService,
)
from agent_platform.capabilities.video_studio.persistence import (
    SqlAlchemyMediaLibraryRepository,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialObjectCleaner,
)

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True, slots=True)
class MediaLibrarySweepReport:
    expired_drafts: int
    cleaned_objects: int
    failed_cleanups: int


class _MaintenanceProviderMisuse:
    """维护清扫绝不签发凭证或核验对象；被调用即为装配错误。"""

    async def issue_upload_credentials(self, **kwargs: Any) -> Any:
        raise RuntimeError("maintenance sweep must not issue upload credentials")

    async def inspect_uploaded_object(self, **kwargs: Any) -> Any:
        raise RuntimeError("maintenance sweep must not verify uploaded objects")


def _service(
    session: AsyncSession,
    *,
    object_cleaner: MaterialObjectCleaner | None,
    clock: Callable[[], datetime] | None,
) -> MediaLibraryService:
    misuse = _MaintenanceProviderMisuse()
    return MediaLibraryService(
        repository=SqlAlchemyMediaLibraryRepository(session),
        credential_issuer=misuse,
        object_verifier=misuse,
        object_cleaner=object_cleaner,
        clock=clock,
    )


async def sweep_media_library_once(
    *,
    session_factory: SessionFactory,
    object_cleaner: MaterialObjectCleaner | None,
    batch_limit: int,
    clock: Callable[[], datetime] | None = None,
) -> MediaLibrarySweepReport:
    # 1) 过期草稿止血：单事务批量标记 upload_failed + cleanup_required。
    async with session_factory() as session:
        expired = await _service(
            session, object_cleaner=None, clock=clock
        ).expire_upload_drafts(limit=batch_limit)
        await session.commit()

    cleaned_objects = 0
    failed_cleanups = 0
    if object_cleaner is None:
        if expired:
            logger.info(
                "video_material_cleanup_skipped_no_cleaner",
                extra={"pending_cleanups": len(expired)},
            )
        return MediaLibrarySweepReport(
            expired_drafts=len(expired),
            cleaned_objects=0,
            failed_cleanups=0,
        )

    # 2) 对象回收：逐素材独立事务，单个失败保留标记、下一轮重试。
    async with session_factory() as session:
        targets = await SqlAlchemyMediaLibraryRepository(
            session
        ).list_cleanup_required_materials(limit=batch_limit)
    for target in targets:
        try:
            async with session_factory() as session:
                await _service(
                    session, object_cleaner=object_cleaner, clock=clock
                ).cleanup_material_object(
                    tenant_id=target.tenant_id,
                    material_id=target.id,
                )
                await session.commit()
            cleaned_objects += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            failed_cleanups += 1
            logger.warning(
                "video_material_object_cleanup_failed",
                extra={
                    "tenant_id": str(target.tenant_id),
                    "material_id": str(target.id),
                },
            )
    return MediaLibrarySweepReport(
        expired_drafts=len(expired),
        cleaned_objects=cleaned_objects,
        failed_cleanups=failed_cleanups,
    )


async def run_media_library_maintenance(
    *,
    session_factory: SessionFactory,
    object_cleaner: MaterialObjectCleaner | None,
    interval_seconds: float,
    batch_limit: int,
) -> None:
    """常驻维护循环：固定间隔清扫，进程停机时随 lifespan 取消退出。"""

    while True:
        try:
            report = await sweep_media_library_once(
                session_factory=session_factory,
                object_cleaner=object_cleaner,
                batch_limit=batch_limit,
            )
            if report.expired_drafts or report.cleaned_objects or report.failed_cleanups:
                logger.info(
                    "video_media_library_sweep_completed",
                    extra={
                        "expired_drafts": report.expired_drafts,
                        "cleaned_objects": report.cleaned_objects,
                        "failed_cleanups": report.failed_cleanups,
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("video_media_library_maintenance_failed")
        await asyncio.sleep(interval_seconds)
