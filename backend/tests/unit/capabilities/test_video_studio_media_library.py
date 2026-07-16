from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.capabilities.video_studio.media_library import (
    DownloadTaskConcurrentUpdateError,
    DownloadTaskStatus,
    InMemoryMaterialRepository,
    InvalidDownloadTaskTransition,
    InvalidMaterialInput,
    MaterialFolderNotFoundError,
    MaterialInUseError,
    MaterialKind,
    MaterialNotFoundError,
    MediaLibraryService,
    UploadCredentialExpiredError,
)
from agent_platform.capabilities.video_studio.storage_credentials import (
    IssuedMaterialPreview,
    IssuedUploadCredentials,
    StoredMaterialObject,
)


class RecordingCredentialIssuer:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    async def issue_upload_credentials(
        self,
        *,
        tenant_id,
        key_prefix: str,
        expires_at: datetime,
        allowed_actions: tuple[str, ...],
    ) -> IssuedUploadCredentials:
        self.requests.append(
            {
                "tenant_id": tenant_id,
                "key_prefix": key_prefix,
                "expires_at": expires_at,
                "allowed_actions": allowed_actions,
            }
        )
        return IssuedUploadCredentials(
            provider="tencent-cos",
            bucket="agent-platform-materials",
            region="ap-beijing",
            key_prefix=key_prefix,
            tmp_secret_id="tmp-secret-id",
            tmp_secret_key="tmp-secret-key",
            session_token="session-token",
            expires_at=expires_at,
        )


class RecordingObjectVerifier:
    def __init__(self) -> None:
        self.objects: dict[tuple[object, str], StoredMaterialObject] = {}
        self.requests: list[tuple[object, str]] = []

    async def inspect_uploaded_object(self, *, tenant_id, object_key: str) -> StoredMaterialObject:
        self.requests.append((tenant_id, object_key))
        return self.objects[(tenant_id, object_key)]


class FlakyObjectCleaner:
    def __init__(self) -> None:
        self.requests: list[tuple[object, str]] = []
        self.failures_remaining = 1

    async def delete_object(self, *, tenant_id, object_key: str) -> None:
        self.requests.append((tenant_id, object_key))
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise OSError("temporary object storage failure")


class RecordingPreviewIssuer:
    def __init__(self) -> None:
        self.requests: list[tuple[object, str, datetime]] = []

    async def issue_preview_url(
        self,
        *,
        tenant_id,
        object_key: str,
        expires_at: datetime,
    ) -> IssuedMaterialPreview:
        self.requests.append((tenant_id, object_key, expires_at))
        return IssuedMaterialPreview(
            url=f"https://preview.invalid/{object_key}",
            expires_at=expires_at,
        )


class ConflictingDownloadRepository(InMemoryMaterialRepository):
    async def update_download_task(self, task, *, expected_revision: int) -> bool:
        del task, expected_revision
        return False


@pytest.mark.asyncio
async def test_upload_credentials_are_short_lived_and_scoped_to_tenant_material_prefix() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    now = datetime(2026, 7, 16, 9, 0, tzinfo=UTC)
    issuer = RecordingCredentialIssuer()
    service = MediaLibraryService.in_memory(
        credential_issuer=issuer,
        object_verifier=RecordingObjectVerifier(),
        clock=lambda: now,
    )

    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="launch.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=256 * 1024 * 1024,
        sha256="a" * 64,
        tag_names=("广告", "7月"),
    )

    assert draft.material.status == "pending_upload"
    assert draft.material.storage_key == (
        f"materials/{tenant_id}/{draft.material.id}/launch.mp4"
    )
    assert draft.credentials.key_prefix == f"materials/{tenant_id}/{draft.material.id}/"
    assert draft.credentials.expires_at == now + timedelta(minutes=15)
    assert issuer.requests == [
        {
            "tenant_id": tenant_id,
            "key_prefix": f"materials/{tenant_id}/{draft.material.id}/",
            "expires_at": now + timedelta(minutes=15),
            "allowed_actions": (
                "name/cos:PutObject",
                "name/cos:PostObject",
                "name/cos:InitiateMultipartUpload",
                "name/cos:UploadPart",
                "name/cos:CompleteMultipartUpload",
                "name/cos:AbortMultipartUpload",
            ),
        }
    ]


@pytest.mark.asyncio
async def test_material_folders_are_tenant_scoped_and_upload_requires_existing_folder() -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    owner_id = uuid4()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=RecordingObjectVerifier(),
        clock=lambda: datetime(2026, 7, 16, 8, 30, tzinfo=UTC),
    )

    root = await service.create_folder(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="广告素材",
    )
    child = await service.create_folder(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="7月",
        parent_id=root.id,
    )

    assert [folder.name for folder in await service.list_folders(tenant_id=tenant_id)] == [
        "广告素材",
        "7月",
    ]
    with pytest.raises(MaterialFolderNotFoundError):
        await service.list_folders(tenant_id=other_tenant_id, parent_id=root.id)

    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="launch.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=256 * 1024 * 1024,
        sha256="a" * 64,
        folder_id=child.id,
    )
    assert draft.material.folder_id == child.id

    with pytest.raises(MaterialFolderNotFoundError):
        await service.request_upload_credentials(
            tenant_id=other_tenant_id,
            actor_id=owner_id,
            name="leak.mp4",
            kind=MaterialKind.VIDEO,
            media_type="video/mp4",
            size_bytes=1024,
            sha256="b" * 64,
            folder_id=child.id,
        )


@pytest.mark.asyncio
async def test_complete_upload_rejects_cross_tenant_and_expired_credentials() -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    owner_id = uuid4()
    current_time = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    issuer = RecordingCredentialIssuer()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=issuer,
        object_verifier=verifier,
        clock=lambda: current_time,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="voice.mp3",
        kind=MaterialKind.MUSIC,
        media_type="audio/mpeg",
        size_bytes=12_345,
        sha256="b" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=12_345,
        sha256="b" * 64,
    )

    with pytest.raises(MaterialNotFoundError):
        await service.complete_upload(
            tenant_id=other_tenant_id,
            actor_id=owner_id,
            material_id=draft.material.id,
        )

    current_time = draft.credentials.expires_at + timedelta(seconds=1)
    with pytest.raises(UploadCredentialExpiredError):
        await service.complete_upload(
            tenant_id=tenant_id,
            actor_id=owner_id,
            material_id=draft.material.id,
        )
    failed = await service.get_material(tenant_id=tenant_id, material_id=draft.material.id)
    assert failed.status == "upload_failed"
    assert failed.cleanup_required is True


@pytest.mark.asyncio
async def test_referenced_material_cannot_be_deleted_until_reference_is_removed() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        clock=lambda: datetime(2026, 7, 16, 11, 0, tzinfo=UTC),
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="cover.png",
        kind=MaterialKind.IMAGE,
        media_type="image/png",
        size_bytes=4096,
        sha256="c" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=4096,
        sha256="c" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )
    await service.add_reference(
        tenant_id=tenant_id,
        material_id=material.id,
        reference_type="timeline",
        reference_id=uuid4(),
    )

    with pytest.raises(MaterialInUseError):
        await service.delete_material(
            tenant_id=tenant_id,
            actor_id=owner_id,
            material_id=material.id,
        )


@pytest.mark.asyncio
async def test_download_task_progress_resume_failure_and_retry_state_machine() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        clock=lambda: datetime(2026, 7, 16, 12, 0, tzinfo=UTC),
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="cut.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="d" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="d" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )

    task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )
    assert task.status == DownloadTaskStatus.QUEUED

    running = await service.start_download_task(tenant_id=tenant_id, task_id=task.id)
    assert running.status == DownloadTaskStatus.RUNNING
    progressed = await service.update_download_progress(
        tenant_id=tenant_id,
        task_id=task.id,
        downloaded_bytes=400,
        resume_token="bytes=400-",
    )
    assert progressed.progress == 40
    assert progressed.resume_token == "bytes=400-"
    failed = await service.fail_download_task(
        tenant_id=tenant_id,
        task_id=task.id,
        error_code="network_timeout",
        retryable=True,
    )
    assert failed.status == DownloadTaskStatus.FAILED
    retried = await service.retry_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        task_id=task.id,
    )
    assert retried.status == DownloadTaskStatus.QUEUED
    assert retried.retry_count == 1
    assert retried.downloaded_bytes == 400
    assert retried.resume_token == "bytes=400-"

    with pytest.raises(InvalidDownloadTaskTransition):
        await service.retry_download_task(
            tenant_id=tenant_id,
            actor_id=owner_id,
            task_id=task.id,
        )


@pytest.mark.asyncio
async def test_complete_upload_uses_trusted_object_metadata_instead_of_client_claims() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        clock=lambda: datetime(2026, 7, 16, 13, 0, tzinfo=UTC),
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="trusted.png",
        kind=MaterialKind.IMAGE,
        media_type="image/png",
        size_bytes=2048,
        sha256="e" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=2048,
        sha256="e" * 64,
    )

    completed = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )

    assert completed.status == "available"
    assert verifier.requests == [(tenant_id, draft.material.storage_key)]


@pytest.mark.asyncio
async def test_download_task_can_complete_or_cancel_without_forging_terminal_state() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        clock=lambda: datetime(2026, 7, 16, 14, 0, tzinfo=UTC),
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="download.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="f" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="f" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )
    completed_task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )
    await service.start_download_task(tenant_id=tenant_id, task_id=completed_task.id)

    completed = await service.complete_download_task(
        tenant_id=tenant_id,
        task_id=completed_task.id,
    )

    assert completed.status == DownloadTaskStatus.SUCCEEDED
    assert completed.progress == 100
    assert completed.downloaded_bytes == completed.total_bytes
    assert completed.resume_token is None
    assert completed.completed_at is not None

    cancelled_task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )
    cancelled = await service.cancel_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        task_id=cancelled_task.id,
    )
    assert cancelled.status == DownloadTaskStatus.CANCELLED
    assert cancelled.completed_at is not None

    with pytest.raises(InvalidDownloadTaskTransition):
        await service.fail_download_task(
            tenant_id=tenant_id,
            task_id=completed_task.id,
            error_code="late_failure",
            retryable=True,
        )


@pytest.mark.asyncio
async def test_download_progress_rejects_corrupt_or_regressive_worker_updates() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="bounded.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="1" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="1" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )
    task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )
    await service.start_download_task(tenant_id=tenant_id, task_id=task.id)
    await service.update_download_progress(
        tenant_id=tenant_id,
        task_id=task.id,
        downloaded_bytes=400,
        resume_token="bytes=400-",
    )

    for invalid_bytes in (-1, 399, 1001):
        with pytest.raises(InvalidMaterialInput):
            await service.update_download_progress(
                tenant_id=tenant_id,
                task_id=task.id,
                downloaded_bytes=invalid_bytes,
                resume_token="bytes=invalid-",
            )
    with pytest.raises(InvalidMaterialInput):
        await service.update_download_progress(
            tenant_id=tenant_id,
            task_id=task.id,
            downloaded_bytes=500,
            resume_token="x" * 501,
        )
    with pytest.raises(InvalidMaterialInput):
        await service.fail_download_task(
            tenant_id=tenant_id,
            task_id=task.id,
            error_code="INVALID ERROR CODE",
            retryable=True,
        )


@pytest.mark.asyncio
async def test_download_task_user_actions_are_owner_scoped() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    other_member_id = uuid4()
    verifier = RecordingObjectVerifier()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="private.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="2" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="2" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )
    task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )

    with pytest.raises(MaterialNotFoundError):
        await service.cancel_download_task(
            tenant_id=tenant_id,
            actor_id=other_member_id,
            task_id=task.id,
        )
    cancelled = await service.cancel_download_task(
        tenant_id=tenant_id,
        actor_id=other_member_id,
        task_id=task.id,
        manage_all=True,
    )
    assert cancelled.status is DownloadTaskStatus.CANCELLED


@pytest.mark.asyncio
async def test_failed_upload_cleanup_remains_durable_and_can_be_retried() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    cleaner = FlakyObjectCleaner()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        object_cleaner=cleaner,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="corrupt.png",
        kind=MaterialKind.IMAGE,
        media_type="image/png",
        size_bytes=1000,
        sha256="3" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=999,
        sha256="4" * 64,
    )
    with pytest.raises(InvalidMaterialInput):
        await service.complete_upload(
            tenant_id=tenant_id,
            actor_id=owner_id,
            material_id=draft.material.id,
        )

    with pytest.raises(OSError, match="temporary object storage failure"):
        await service.cleanup_material_object(
            tenant_id=tenant_id,
            material_id=draft.material.id,
        )
    still_pending = await service.get_material(
        tenant_id=tenant_id,
        material_id=draft.material.id,
    )
    assert still_pending.cleanup_required is True

    cleaned = await service.cleanup_material_object(
        tenant_id=tenant_id,
        material_id=draft.material.id,
    )
    assert cleaned.cleanup_required is False
    assert cleaner.requests == [
        (tenant_id, draft.material.storage_key),
        (tenant_id, draft.material.storage_key),
    ]


@pytest.mark.asyncio
async def test_preview_url_is_short_lived_and_scoped_to_material_storage_key() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    now = datetime(2026, 7, 16, 15, 0, tzinfo=UTC)
    verifier = RecordingObjectVerifier()
    preview_issuer = RecordingPreviewIssuer()
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        preview_issuer=preview_issuer,
        clock=lambda: now,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="preview.jpg",
        kind=MaterialKind.IMAGE,
        media_type="image/jpeg",
        size_bytes=1000,
        sha256="5" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="5" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )

    preview = await service.request_preview_url(
        tenant_id=tenant_id,
        material_id=material.id,
    )

    assert preview.expires_at == now + timedelta(minutes=5)
    assert preview_issuer.requests == [
        (tenant_id, material.storage_key, now + timedelta(minutes=5)),
    ]
    with pytest.raises(MaterialNotFoundError):
        await service.request_preview_url(
            tenant_id=uuid4(),
            material_id=material.id,
        )


@pytest.mark.asyncio
async def test_aborted_and_expired_upload_drafts_are_marked_for_cleanup() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    current_time = datetime(2026, 7, 16, 16, 0, tzinfo=UTC)
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=RecordingObjectVerifier(),
        clock=lambda: current_time,
    )
    aborted_draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="aborted.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="6" * 64,
    )
    aborted = await service.abort_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=aborted_draft.material.id,
    )
    assert aborted.status == "upload_failed"
    assert aborted.cleanup_required is True

    expired_draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="expired.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="7" * 64,
    )
    current_time = expired_draft.credentials.expires_at + timedelta(seconds=1)
    expired = await service.expire_upload_drafts(limit=10)

    assert [material.id for material in expired] == [expired_draft.material.id]
    assert expired[0].status == "upload_failed"
    assert expired[0].cleanup_required is True


@pytest.mark.asyncio
async def test_download_state_transition_fails_on_concurrent_revision_change() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    repository = ConflictingDownloadRepository()
    service = MediaLibraryService(
        repository=repository,
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
    )
    draft = await service.request_upload_credentials(
        tenant_id=tenant_id,
        actor_id=owner_id,
        name="race.mp4",
        kind=MaterialKind.VIDEO,
        media_type="video/mp4",
        size_bytes=1000,
        sha256="9" * 64,
    )
    verifier.objects[(tenant_id, draft.material.storage_key)] = StoredMaterialObject(
        size_bytes=1000,
        sha256="9" * 64,
    )
    material = await service.complete_upload(
        tenant_id=tenant_id,
        actor_id=owner_id,
        material_id=draft.material.id,
    )
    task = await service.create_download_task(
        tenant_id=tenant_id,
        actor_id=owner_id,
        source_type="material",
        source_id=material.id,
    )

    with pytest.raises(DownloadTaskConcurrentUpdateError):
        await service.start_download_task(tenant_id=tenant_id, task_id=task.id)


@pytest.mark.asyncio
async def test_list_references_is_tenant_scoped_and_ordered_by_creation() -> None:
    tenant_id = uuid4()
    other_tenant_id = uuid4()
    owner_id = uuid4()
    verifier = RecordingObjectVerifier()
    moments = iter(
        datetime(2026, 7, 16, 11, minute, tzinfo=UTC) for minute in range(30)
    )
    service = MediaLibraryService.in_memory(
        credential_issuer=RecordingCredentialIssuer(),
        object_verifier=verifier,
        clock=lambda: next(moments),
    )

    async def make_material(target_tenant_id, name: str):
        draft = await service.request_upload_credentials(
            tenant_id=target_tenant_id,
            actor_id=owner_id,
            name=name,
            kind=MaterialKind.VIDEO,
            media_type="video/mp4",
            size_bytes=1024,
            sha256="d" * 64,
        )
        verifier.objects[(target_tenant_id, draft.material.storage_key)] = StoredMaterialObject(
            size_bytes=1024,
            sha256="d" * 64,
        )
        return await service.complete_upload(
            tenant_id=target_tenant_id,
            actor_id=owner_id,
            material_id=draft.material.id,
        )

    material = await make_material(tenant_id, "list-refs.mp4")
    other_material = await make_material(other_tenant_id, "other.mp4")

    first = await service.add_reference(
        tenant_id=tenant_id,
        material_id=material.id,
        reference_type="timeline_clip",
        reference_id=uuid4(),
    )
    second = await service.add_reference(
        tenant_id=tenant_id,
        material_id=material.id,
        reference_type="render_job",
        reference_id=uuid4(),
    )
    await service.add_reference(
        tenant_id=other_tenant_id,
        material_id=other_material.id,
        reference_type="timeline_clip",
        reference_id=uuid4(),
    )

    listed = await service.list_references(tenant_id=tenant_id, material_id=material.id)
    assert [reference.id for reference in listed] == [first.id, second.id]

    with pytest.raises(MaterialNotFoundError):
        await service.list_references(tenant_id=other_tenant_id, material_id=material.id)
