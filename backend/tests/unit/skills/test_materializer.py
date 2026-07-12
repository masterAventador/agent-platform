from datetime import UTC, datetime
from io import BytesIO
from uuid import UUID, uuid4
from zipfile import ZipFile

import pytest

from agent_platform.platform.skills.bundle import parse_skill_bundle
from agent_platform.platform.skills.entities import Skill, SkillVersion
from agent_platform.platform.skills.errors import SkillNotFound, SkillVersionNotFound
from agent_platform.platform.skills.materializer import (
    SkillBundleDigestMismatch,
    SkillMaterializer,
    SkillNotPublished,
)


class FakePublishedSkillRepository:
    def __init__(self) -> None:
        self.skills: dict[tuple[UUID, UUID], Skill] = {}
        self.versions: dict[tuple[UUID, UUID, int], SkillVersion] = {}
        self.requested_skill_ids: list[UUID] = []

    async def get(self, *, tenant_id: UUID, skill_id: UUID) -> Skill | None:
        self.requested_skill_ids.append(skill_id)
        return self.skills.get((tenant_id, skill_id))

    async def get_version(
        self,
        *,
        tenant_id: UUID,
        skill_id: UUID,
        version: int,
    ) -> SkillVersion | None:
        return self.versions.get((tenant_id, skill_id, version))


class FakeSkillStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.requested_keys: list[str] = []

    async def put(self, *, key: str, content: bytes) -> None:
        self.objects[key] = content

    async def get(self, *, key: str) -> bytes:
        self.requested_keys.append(key)
        return self.objects[key]

    async def delete(self, *, key: str) -> None:
        self.objects.pop(key, None)


class RecordingWorkspace:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    async def write_file(self, *, path: str, content: bytes) -> None:
        self.files[path] = content


def _archive(*, name: str, reference: str) -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: {name} description.\n---\n".encode(),
        )
        archive.writestr(f"{name}/references/guide.md", reference.encode())
    return output.getvalue()


def _published_skill(
    *,
    tenant_id: UUID,
    skill_id: UUID,
    name: str,
    archive: bytes,
    version_number: int = 1,
) -> tuple[Skill, SkillVersion]:
    now = datetime.now(UTC)
    bundle = parse_skill_bundle(archive)
    skill = Skill(
        id=skill_id,
        tenant_id=tenant_id,
        name=name,
        description=bundle.description,
        latest_version=version_number,
        published_version=version_number,
        created_by=uuid4(),
        created_at=now,
        updated_at=now,
    )
    version = SkillVersion(
        id=uuid4(),
        skill_id=skill_id,
        tenant_id=tenant_id,
        version=version_number,
        description=bundle.description,
        digest=bundle.digest,
        files=bundle.files,
        storage_key=f"objects/{skill_id}/{version_number}.zip",
        created_by=skill.created_by,
        created_at=now,
        published_at=now,
    )
    return skill, version


def _register(
    repository: FakePublishedSkillRepository,
    storage: FakeSkillStorage,
    skill: Skill,
    version: SkillVersion,
    archive: bytes,
) -> None:
    repository.skills[(skill.tenant_id, skill.id)] = skill
    repository.versions[(skill.tenant_id, skill.id, version.version)] = version
    storage.objects[version.storage_key] = archive


@pytest.mark.asyncio
async def test_materialize_published_skills_deduplicates_in_order_and_writes_bundle() -> None:
    tenant_id = uuid4()
    repository = FakePublishedSkillRepository()
    storage = FakeSkillStorage()
    workspace = RecordingWorkspace()
    first_archive = _archive(name="report-writer", reference="Write concise reports.")
    second_archive = _archive(name="web-research", reference="Cite primary sources.")
    first = _published_skill(
        tenant_id=tenant_id,
        skill_id=uuid4(),
        name="report-writer",
        archive=first_archive,
    )
    second = _published_skill(
        tenant_id=tenant_id,
        skill_id=uuid4(),
        name="web-research",
        archive=second_archive,
        version_number=3,
    )
    _register(repository, storage, *first, first_archive)
    _register(repository, storage, *second, second_archive)
    materializer = SkillMaterializer(repository=repository, storage=storage)

    paths = await materializer.materialize(
        tenant_id=tenant_id,
        skill_ids=[first[0].id, second[0].id, first[0].id],
        workspace=workspace,
    )

    expected_first = f"/skills/{tenant_id}/{first[0].id}/v1"
    expected_second = f"/skills/{tenant_id}/{second[0].id}/v3"
    assert paths == [expected_first, expected_second]
    assert repository.requested_skill_ids == [first[0].id, second[0].id]
    assert workspace.files[f"{expected_first}/SKILL.md"].startswith(b"---\n")
    assert workspace.files[f"{expected_first}/references/guide.md"] == (
        b"Write concise reports."
    )
    assert workspace.files[f"{expected_second}/references/guide.md"] == (
        b"Cite primary sources."
    )


@pytest.mark.asyncio
async def test_materialize_rejects_skill_from_another_tenant() -> None:
    owner_tenant_id = uuid4()
    current_tenant_id = uuid4()
    archive = _archive(name="tenant-secret", reference="Secret instructions.")
    skill, version = _published_skill(
        tenant_id=owner_tenant_id,
        skill_id=uuid4(),
        name="tenant-secret",
        archive=archive,
    )
    repository = FakePublishedSkillRepository()
    storage = FakeSkillStorage()
    workspace = RecordingWorkspace()
    _register(repository, storage, skill, version, archive)
    materializer = SkillMaterializer(repository=repository, storage=storage)

    with pytest.raises(SkillNotFound):
        await materializer.materialize(
            tenant_id=current_tenant_id,
            skill_ids=[skill.id],
            workspace=workspace,
        )

    assert storage.requested_keys == []
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_materialize_rejects_unpublished_skill() -> None:
    tenant_id = uuid4()
    archive = _archive(name="draft-skill", reference="Not reviewed.")
    skill, version = _published_skill(
        tenant_id=tenant_id,
        skill_id=uuid4(),
        name="draft-skill",
        archive=archive,
    )
    skill = Skill(**{**skill.__dict__, "published_version": None})
    repository = FakePublishedSkillRepository()
    repository.skills[(tenant_id, skill.id)] = skill
    storage = FakeSkillStorage()
    workspace = RecordingWorkspace()
    materializer = SkillMaterializer(repository=repository, storage=storage)

    with pytest.raises(SkillNotPublished):
        await materializer.materialize(
            tenant_id=tenant_id,
            skill_ids=[skill.id],
            workspace=workspace,
        )

    assert storage.requested_keys == []
    assert workspace.files == {}


@pytest.mark.asyncio
async def test_materialize_rejects_missing_published_version() -> None:
    tenant_id = uuid4()
    archive = _archive(name="missing-version", reference="Missing.")
    skill, _ = _published_skill(
        tenant_id=tenant_id,
        skill_id=uuid4(),
        name="missing-version",
        archive=archive,
    )
    repository = FakePublishedSkillRepository()
    repository.skills[(tenant_id, skill.id)] = skill
    storage = FakeSkillStorage()
    materializer = SkillMaterializer(repository=repository, storage=storage)

    with pytest.raises(SkillVersionNotFound):
        await materializer.materialize(
            tenant_id=tenant_id,
            skill_ids=[skill.id],
            workspace=RecordingWorkspace(),
        )


@pytest.mark.asyncio
async def test_materialize_verifies_digest_before_writing_files() -> None:
    tenant_id = uuid4()
    archive = _archive(name="verified-skill", reference="Trusted content.")
    skill, version = _published_skill(
        tenant_id=tenant_id,
        skill_id=uuid4(),
        name="verified-skill",
        archive=archive,
    )
    version = SkillVersion(**{**version.__dict__, "digest": "0" * 64})
    repository = FakePublishedSkillRepository()
    storage = FakeSkillStorage()
    workspace = RecordingWorkspace()
    _register(repository, storage, skill, version, archive)
    materializer = SkillMaterializer(repository=repository, storage=storage)

    with pytest.raises(SkillBundleDigestMismatch):
        await materializer.materialize(
            tenant_id=tenant_id,
            skill_ids=[skill.id],
            workspace=workspace,
        )

    assert workspace.files == {}
