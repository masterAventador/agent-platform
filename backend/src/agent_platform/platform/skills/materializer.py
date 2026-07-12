from __future__ import annotations

from collections.abc import Sequence
from hmac import compare_digest
from pathlib import PurePosixPath
from typing import Protocol
from uuid import UUID

from agent_platform.platform.skills.bundle import SkillBundle, parse_skill_bundle
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.errors import SkillNotFound, SkillVersionNotFound
from agent_platform.platform.skills.ports import SkillStorage


class PublishedSkillRepository(Protocol):
    async def get(self, *, tenant_id: UUID, skill_id: UUID) -> Skill | None: ...

    async def get_version(
        self,
        *,
        tenant_id: UUID,
        skill_id: UUID,
        version: int,
    ) -> SkillVersion | None: ...


class SkillWorkspace(Protocol):
    async def write_file(self, *, path: str, content: bytes) -> None: ...


class SkillNotPublished(Exception):
    """当前租户中的 Skill 尚未发布。"""


class SkillBundleDigestMismatch(Exception):
    """对象存储中的 Skill 包与 Registry 摘要不一致。"""


class SkillMaterializer:
    def __init__(
        self,
        *,
        repository: PublishedSkillRepository,
        storage: SkillStorage,
    ) -> None:
        self._repository = repository
        self._storage = storage

    async def materialize(
        self,
        *,
        tenant_id: UUID,
        skill_ids: Sequence[UUID],
        workspace: SkillWorkspace,
    ) -> list[str]:
        resolved: list[tuple[str, SkillBundle]] = []
        for skill_id in dict.fromkeys(skill_ids):
            skill = await self._repository.get(tenant_id=tenant_id, skill_id=skill_id)
            if skill is None:
                raise SkillNotFound
            if skill.published_version is None:
                raise SkillNotPublished
            version = await self._repository.get_version(
                tenant_id=tenant_id,
                skill_id=skill_id,
                version=skill.published_version,
            )
            if version is None:
                raise SkillVersionNotFound
            archive = await self._storage.get(key=version.storage_key)
            bundle = parse_skill_bundle(archive)
            if not compare_digest(bundle.digest, version.digest):
                raise SkillBundleDigestMismatch
            root = PurePosixPath(
                "/skills",
                str(tenant_id),
                str(skill.id),
                f"v{version.version}",
            ).as_posix()
            resolved.append((root, bundle))

        for root, bundle in resolved:
            for path in bundle.files:
                target = PurePosixPath(root, path).as_posix()
                await workspace.write_file(path=target, content=bundle.read_bytes(path))
        return [root for root, _ in resolved]
