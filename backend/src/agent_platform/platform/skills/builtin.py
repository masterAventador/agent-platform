from __future__ import annotations

from io import BytesIO
from pathlib import Path
from uuid import UUID
from zipfile import ZipFile

from agent_platform.platform.skills.bundle import parse_skill_bundle
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.errors import SkillReviewBlocked
from agent_platform.platform.skills.ports import SkillRepository, SkillStorage
from agent_platform.platform.skills.security import SkillReviewStatus


class BuiltinSkillInstaller:
    def __init__(
        self,
        *,
        repository: SkillRepository,
        storage: SkillStorage,
        root: Path,
    ) -> None:
        self._repository = repository
        self._storage = storage
        self._root = root

    async def install_all(self, *, tenant_id: UUID, created_by: UUID) -> list[Skill]:
        if not self._root.exists():
            return []
        installed: list[Skill] = []
        for skill_dir in sorted(path for path in self._root.iterdir() if path.is_dir()):
            installed.append(
                await self._install_one(
                    tenant_id=tenant_id,
                    created_by=created_by,
                    skill_dir=skill_dir,
                )
            )
        return installed

    async def _install_one(
        self,
        *,
        tenant_id: UUID,
        created_by: UUID,
        skill_dir: Path,
    ) -> Skill:
        content = _zip_skill_dir(skill_dir)
        bundle = parse_skill_bundle(content)
        existing = await self._repository.get_by_name(
            tenant_id=tenant_id,
            name=bundle.name,
        )
        if existing is None:
            skill = Skill.create(
                tenant_id=tenant_id,
                created_by=created_by,
                bundle=bundle,
                source="builtin",
            )
            version = self._published_version(
                skill=skill,
                version=1,
                bundle_content=content,
                bundle_digest=bundle.digest,
                created_by=created_by,
            )
            await self._storage.put(key=version.storage_key, content=content)
            await self._repository.add(skill.publish(1), version)
            return skill.publish(1)

        latest = await self._repository.get_version(
            tenant_id=tenant_id,
            skill_id=existing.id,
            version=existing.latest_version,
        )
        if latest is not None and latest.digest == bundle.digest:
            return existing

        updated = existing.add_version(bundle)
        version = self._published_version(
            skill=existing,
            version=updated.latest_version,
            bundle_content=content,
            bundle_digest=bundle.digest,
            created_by=created_by,
        )
        await self._storage.put(key=version.storage_key, content=content)
        await self._repository.add_version(version)
        await self._repository.update_version(version)
        published = updated.publish(version.version)
        await self._repository.update(published)
        return published

    @staticmethod
    def _published_version(
        *,
        skill: Skill,
        version: int,
        bundle_content: bytes,
        bundle_digest: str,
        created_by: UUID,
    ) -> SkillVersion:
        bundle = parse_skill_bundle(bundle_content)
        storage_key = (
            f"tenants/{skill.tenant_id}/skills/{skill.id}/"
            f"versions/{version}/{bundle_digest}.zip"
        )
        value = SkillVersion.create(
            skill=skill,
            version=version,
            bundle=bundle,
            storage_key=storage_key,
            created_by=created_by,
        )
        if value.review_status is not SkillReviewStatus.APPROVED:
            raise SkillReviewBlocked
        return value.publish()


def _zip_skill_dir(skill_dir: Path) -> bytes:
    output = BytesIO()
    root_name = skill_dir.name
    with ZipFile(output, "w") as archive:
        for path in sorted(item for item in skill_dir.rglob("*") if item.is_file()):
            archive.write(path, arcname=f"{root_name}/{path.relative_to(skill_dir).as_posix()}")
    return output.getvalue()
