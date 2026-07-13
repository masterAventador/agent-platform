from __future__ import annotations

from uuid import UUID

from agent_platform.platform.skills.bundle import SkillBundle, SkillBundleError, parse_skill_bundle
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.errors import (
    SkillNameMismatch,
    SkillNotFound,
    SkillVersionNotFound,
)
from agent_platform.platform.skills.ports import SkillRepository, SkillStorage


class SkillService:
    def __init__(self, *, repository: SkillRepository, storage: SkillStorage) -> None:
        self._repository = repository
        self._storage = storage

    async def create(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        content: bytes,
    ) -> tuple[Skill, SkillVersion]:
        bundle = parse_skill_bundle(content)
        skill = Skill.create(tenant_id=tenant_id, created_by=created_by, bundle=bundle)
        version = self._version(
            skill=skill,
            version=1,
            bundle=bundle,
            created_by=created_by,
        )
        await self._storage.put(key=version.storage_key, content=content)
        try:
            await self._repository.add(skill, version)
        except Exception:
            await self._storage.delete(key=version.storage_key)
            raise
        return skill, version

    async def add_version(
        self,
        *,
        tenant_id: UUID,
        skill_id: UUID,
        created_by: UUID,
        content: bytes,
    ) -> tuple[Skill, SkillVersion]:
        skill = await self.required_skill(tenant_id=tenant_id, skill_id=skill_id)
        bundle = parse_skill_bundle(content)
        if bundle.name != skill.name:
            raise SkillNameMismatch
        updated = skill.add_version(bundle)
        version = self._version(
            skill=skill,
            version=updated.latest_version,
            bundle=bundle,
            created_by=created_by,
        )
        await self._storage.put(key=version.storage_key, content=content)
        try:
            await self._repository.add_version(version)
            await self._repository.update(updated)
        except Exception:
            await self._storage.delete(key=version.storage_key)
            raise
        return updated, version

    async def publish(
        self,
        *,
        tenant_id: UUID,
        skill_id: UUID,
        version_number: int,
    ) -> Skill:
        skill = await self.required_skill(tenant_id=tenant_id, skill_id=skill_id)
        version = await self._repository.get_version(
            tenant_id=tenant_id,
            skill_id=skill_id,
            version=version_number,
        )
        if version is None:
            raise SkillVersionNotFound
        published = skill.publish(version_number)
        await self._repository.update(published)
        await self._repository.update_version(version.publish())
        return published

    async def required_skill(self, *, tenant_id: UUID, skill_id: UUID) -> Skill:
        skill = await self._repository.get(tenant_id=tenant_id, skill_id=skill_id)
        if skill is None:
            raise SkillNotFound
        return skill

    async def list_all(self, *, tenant_id: UUID) -> list[Skill]:
        return await self._repository.list_all(tenant_id=tenant_id)

    async def required_version(
        self, *, tenant_id: UUID, skill_id: UUID, version: int
    ) -> SkillVersion:
        value = await self._repository.get_version(
            tenant_id=tenant_id,
            skill_id=skill_id,
            version=version,
        )
        if value is None:
            raise SkillVersionNotFound
        return value

    async def list_versions(self, *, tenant_id: UUID, skill_id: UUID) -> list[SkillVersion]:
        await self.required_skill(tenant_id=tenant_id, skill_id=skill_id)
        return await self._repository.list_versions(tenant_id=tenant_id, skill_id=skill_id)

    async def read_file(
        self,
        *,
        tenant_id: UUID,
        skill_id: UUID,
        version_number: int,
        path: str,
    ) -> bytes:
        await self.required_skill(tenant_id=tenant_id, skill_id=skill_id)
        version = await self._repository.get_version(
            tenant_id=tenant_id,
            skill_id=skill_id,
            version=version_number,
        )
        if version is None:
            raise SkillVersionNotFound
        archive = await self._storage.get(key=version.storage_key)
        bundle = parse_skill_bundle(archive)
        if bundle.digest != version.digest:
            raise SkillBundleError("Skill 存储内容摘要不匹配")
        return bundle.read_bytes(path)

    @staticmethod
    def _version(
        *,
        skill: Skill,
        version: int,
        bundle: SkillBundle,
        created_by: UUID,
    ) -> SkillVersion:
        storage_key = (
            f"tenants/{skill.tenant_id}/skills/{skill.id}/"
            f"versions/{version}/{bundle.digest}.zip"
        )
        return SkillVersion.create(
            skill=skill,
            version=version,
            bundle=bundle,
            storage_key=storage_key,
            created_by=created_by,
        )
