from uuid import uuid4

import pytest

from agent_platform.platform.artifacts.entities import (
    MAX_FILE_SIZE_BYTES,
    Artifact,
    File,
    InvalidArtifactInput,
    TaskAttachment,
)


def test_file_uses_server_generated_tenant_scoped_key_and_digest() -> None:
    tenant_id = uuid4()
    owner_id = uuid4()

    file = File.create(
        tenant_id=tenant_id,
        owner_id=owner_id,
        name="需求说明.pdf",
        media_type="application/pdf",
        content=b"contract",
    )

    assert file.storage_key == f"tenants/{tenant_id}/files/{file.id}"
    assert file.name == "需求说明.pdf"
    assert file.size_bytes == 8
    assert len(file.sha256) == 64


@pytest.mark.parametrize("name", ["../secret.txt", "a/b.txt", "a\\b.txt", "", "."])
def test_file_rejects_malicious_or_empty_names(name: str) -> None:
    with pytest.raises(InvalidArtifactInput, match="文件名"):
        File.create(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name=name,
            media_type="text/plain",
            content=b"safe",
        )


def test_file_rejects_disallowed_type_and_oversize() -> None:
    with pytest.raises(InvalidArtifactInput, match="文件类型"):
        File.create(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name="payload.exe",
            media_type="application/x-msdownload",
            content=b"MZ",
        )
    with pytest.raises(InvalidArtifactInput, match="文件大小"):
        File.create(
            tenant_id=uuid4(),
            owner_id=uuid4(),
            name="huge.txt",
            media_type="text/plain",
            content=b"x" * (MAX_FILE_SIZE_BYTES + 1),
        )


def test_attachment_and_artifact_are_bound_to_run_and_tenant() -> None:
    tenant_id = uuid4()
    run_id = uuid4()
    file_id = uuid4()
    creator_id = uuid4()

    attachment = TaskAttachment.create(
        tenant_id=tenant_id,
        run_id=run_id,
        file_id=file_id,
        workspace_path="inputs/brief.txt",
    )
    artifact = Artifact.create(
        tenant_id=tenant_id,
        run_id=run_id,
        created_by=creator_id,
        name="result.csv",
        media_type="text/csv",
        content=b"a,b\n1,2\n",
    )

    assert attachment.workspace_path == "inputs/brief.txt"
    assert artifact.storage_key == f"tenants/{tenant_id}/runs/{run_id}/artifacts/{artifact.id}"


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "inputs/../../secret", "C:\\x"])
def test_attachment_rejects_workspace_path_escape(path: str) -> None:
    with pytest.raises(InvalidArtifactInput, match="工作区路径"):
        TaskAttachment.create(
            tenant_id=uuid4(),
            run_id=uuid4(),
            file_id=uuid4(),
            workspace_path=path,
        )
