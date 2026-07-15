from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest

from agent_platform.platform.skills.builtin import BuiltinSkillInstaller
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.ports import SkillRepository, SkillStorage


class RecordingSkillRepository(SkillRepository):
    def __init__(self) -> None:
        self.skills_by_name: dict[tuple[UUID, str], Skill] = {}
        self.versions: dict[tuple[UUID, UUID, int], SkillVersion] = {}
        self.added_versions: list[SkillVersion] = []

    async def add(self, skill: Skill, version: SkillVersion) -> None:
        self.skills_by_name[(skill.tenant_id, skill.name)] = skill
        self.versions[(skill.tenant_id, skill.id, version.version)] = version
        self.added_versions.append(version)

    async def update(self, skill: Skill) -> None:
        self.skills_by_name[(skill.tenant_id, skill.name)] = skill

    async def add_version(self, version: SkillVersion) -> None:
        self.versions[(version.tenant_id, version.skill_id, version.version)] = version
        self.added_versions.append(version)

    async def update_version(self, version: SkillVersion) -> None:
        self.versions[(version.tenant_id, version.skill_id, version.version)] = version

    async def get(self, *, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        for (current_tenant_id, _), skill in self.skills_by_name.items():
            if current_tenant_id == tenant_id and skill.id == skill_id:
                return skill
        return None

    async def get_by_name(self, *, tenant_id: UUID, name: str) -> Skill | None:
        return self.skills_by_name.get((tenant_id, name))

    async def list_all(self, *, tenant_id: UUID) -> list[Skill]:
        return [
            skill
            for (current_tenant_id, _), skill in self.skills_by_name.items()
            if current_tenant_id == tenant_id
        ]

    async def get_version(
        self, *, tenant_id: UUID, skill_id: UUID, version: int
    ) -> SkillVersion | None:
        return self.versions.get((tenant_id, skill_id, version))

    async def list_versions(self, *, tenant_id: UUID, skill_id: UUID) -> list[SkillVersion]:
        return [
            version
            for (current_tenant_id, current_skill_id, _), version in self.versions.items()
            if current_tenant_id == tenant_id and current_skill_id == skill_id
        ]

    async def are_bindable(self, *, tenant_id: UUID, skill_ids: list[UUID]) -> bool:
        del tenant_id, skill_ids
        return False

    async def published_versions(
        self,
        *,
        tenant_id: UUID,
        skill_ids: list[UUID],
    ) -> dict[UUID, int]:
        del tenant_id, skill_ids
        return {}


class RecordingStorage(SkillStorage):
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_count = 0

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content
        self.put_count += 1

    async def get(self, *, key: str) -> bytes:
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


def _write_builtin(root: Path, *, description: str, reference: str) -> None:
    skill_dir = root / "report-writer"
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: report-writer\ndescription: {description}\n---\n\n# Report writer\n",
        encoding="utf-8",
    )
    (skill_dir / "references" / "guide.md").write_text(reference, encoding="utf-8")


def _archive_names(content: bytes) -> set[str]:
    with ZipFile(BytesIO(content)) as archive:
        return set(archive.namelist())


@pytest.mark.asyncio
async def test_builtin_installer_is_idempotent_and_versions_only_on_content_change(
    tmp_path,
) -> None:
    builtin_root = tmp_path / "skills" / "builtin"
    _write_builtin(builtin_root, description="Create reports.", reference="Version one")
    repository = RecordingSkillRepository()
    storage = RecordingStorage()
    installer = BuiltinSkillInstaller(repository=repository, storage=storage, root=builtin_root)
    tenant_id = uuid4()
    user_id = uuid4()

    first = await installer.install_all(tenant_id=tenant_id, created_by=user_id)
    second = await installer.install_all(tenant_id=tenant_id, created_by=user_id)
    _write_builtin(builtin_root, description="Create better reports.", reference="Version two")
    third = await installer.install_all(tenant_id=tenant_id, created_by=user_id)

    assert [skill.name for skill in first] == ["report-writer"]
    assert [skill.latest_version for skill in second] == [1]
    assert [skill.latest_version for skill in third] == [2]
    assert storage.put_count == 2
    assert [version.version for version in repository.added_versions] == [1, 2]
    assert all(
        "report-writer/SKILL.md" in _archive_names(content)
        for content in storage.objects.values()
    )
