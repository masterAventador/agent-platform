"""真实腾讯 COS 分段上传 + 断点续传 + 断网恢复门禁（B04 剩余验收门禁）。

需要 TEST_COS_* 环境变量（`set -a && source infra/compose/.env.platform && set +a`）；
缺失时跳过，不假绿。

代表性取舍（务必与真实 20GiB 上传的差异一致理解）：
- 本门禁用「强制 1MiB 分段 + 数 MiB 中等文件」触发 **真实多段** 上传，覆盖生产前端
  `cos.uploadFile` 走的同一 COS 代码路径：``create_multipart_upload``（自定义元数据落在
  Init 请求上）→ 多次 ``upload_part`` → ``complete_multipart_upload`` → COS 服务端对
  组装后对象计算 ``x-cos-hash-crc64ecma``。这正是 crc64 抗伪造硬化在分段路径上的核验点，
  也是第二轮复审 Low-① 指出的「单段 put_object 未覆盖分段自定义元数据/crc64」缺口。
- 未覆盖的、仅在真实 20GiB 规模才暴露的风险：单进程内存不随文件规模膨胀（前端流式分片、
  后端只 head_object 读元数据，理论上不膨胀）、超长传输的 STS/连接超时、超大并发分片吞吐。
  这些属于规模/环境压测范畴，不在本功能门禁内。
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import httpx
import pytest

from agent_platform.capabilities.video_studio.storage_credentials import (
    MaterialObjectMissing,
)
from agent_platform.capabilities.video_studio.tencent_cos import (
    CRC64_HASH_HEADER,
    SHA256_METADATA_HEADER,
    TencentCosMaterialObjectCleaner,
    TencentCosMaterialObjectVerifier,
    TencentCosMaterialPreviewUrlIssuer,
    create_cos_client,
)

_PART_SIZE = 1024 * 1024  # COS 分段最小 1MiB（末段可小于）


def _crc64_xz_table() -> list[int]:
    poly = 0xC96C5795D7870F42  # 反射 ECMA-182（CRC-64/XZ，即 COS crc64ecma）
    table: list[int] = []
    for n in range(256):
        crc = n
        for _ in range(8):
            crc = (crc >> 1) ^ poly if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC64_TABLE = _crc64_xz_table()


def crc64ecma(data: bytes) -> str:
    """独立计算 CRC-64/XZ，与 COS 服务端 x-cos-hash-crc64ecma 一致；返回十进制串。"""

    crc = 0xFFFFFFFFFFFFFFFF
    for byte in data:
        crc = _CRC64_TABLE[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return str(crc ^ 0xFFFFFFFFFFFFFFFF)


def _skip_unless_configured() -> dict[str, str]:
    required = {
        "TEST_COS_REGION": os.getenv("TEST_COS_REGION"),
        "TEST_COS_SECRET_ID": os.getenv("TEST_COS_SECRET_ID"),
        "TEST_COS_SECRET_KEY": os.getenv("TEST_COS_SECRET_KEY"),
        "TEST_COS_BUCKET": os.getenv("TEST_COS_BUCKET"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        pytest.skip(f"需要 {', '.join(missing)} 才运行真实腾讯云 COS 分段/续传门禁")
    return {name: value for name, value in required.items() if value}


def _split_parts(content: bytes) -> list[bytes]:
    return [content[i : i + _PART_SIZE] for i in range(0, len(content), _PART_SIZE)]


@pytest.mark.asyncio
async def test_real_cos_multipart_upload_yields_server_crc64_matching_declaration() -> None:
    """分段上传闭环：自定义元数据落 Init、服务端 crc64 与独立声明一致、预览、清理。"""

    env = _skip_unless_configured()
    bucket = env["TEST_COS_BUCKET"]
    client = create_cos_client(
        region=env["TEST_COS_REGION"],
        secret_id=env["TEST_COS_SECRET_ID"],
        secret_key=env["TEST_COS_SECRET_KEY"],
    )
    verifier = TencentCosMaterialObjectVerifier(bucket=bucket, client=client)
    preview_issuer = TencentCosMaterialPreviewUrlIssuer(bucket=bucket, client=client)
    cleaner = TencentCosMaterialObjectCleaner(bucket=bucket, client=client)
    raw: Any = client  # 分段 API 不在核验 Protocol 上；真实客户端具备这些方法

    tenant_id = uuid4()
    object_key = f"materials/{tenant_id}/{uuid4()}/b04-multipart.bin"
    # 2.5 MiB → 强制 3 段（1MiB + 1MiB + 0.5MiB），确保真实多段路径。
    content = bytes((i * 31 + 7) % 256 for i in range(_PART_SIZE * 2 + _PART_SIZE // 2))
    declared_sha256 = hashlib.sha256(content).hexdigest()
    declared_crc64 = crc64ecma(content)
    parts_bytes = _split_parts(content)
    assert len(parts_bytes) >= 2, "测试数据必须触发真实多段上传"

    try:
        created = raw.create_multipart_upload(
            Bucket=bucket,
            Key=object_key,
            Metadata={SHA256_METADATA_HEADER: declared_sha256},
        )
        upload_id = created["UploadId"]
        parts: list[dict[str, object]] = []
        for index, chunk in enumerate(parts_bytes, start=1):
            resp = raw.upload_part(
                Bucket=bucket,
                Key=object_key,
                Body=chunk,
                PartNumber=index,
                UploadId=upload_id,
            )
            parts.append({"PartNumber": index, "ETag": resp["ETag"]})
        raw.complete_multipart_upload(
            Bucket=bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Part": parts},
        )

        stored = await verifier.inspect_uploaded_object(
            tenant_id=tenant_id, object_key=object_key
        )
        # 关键断言：分段组装后的服务端 crc64 == 独立声明；size 亦一致。
        assert stored.size_bytes == len(content)
        assert stored.crc64ecma == declared_crc64

        # 直接读 head 头，确认 crc64ecma 是服务端返回的头而非客户端元数据。
        head = client.head_object(Bucket=bucket, Key=object_key)
        assert str(head[CRC64_HASH_HEADER]) == declared_crc64

        preview = await preview_issuer.issue_preview_url(
            tenant_id=tenant_id,
            object_key=object_key,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        async with httpx.AsyncClient() as http:
            fetched = await http.get(preview.url)
        assert fetched.status_code == 200
        assert crc64ecma(fetched.content) == declared_crc64

        await cleaner.delete_object(tenant_id=tenant_id, object_key=object_key)
        with pytest.raises(MaterialObjectMissing):
            await verifier.inspect_uploaded_object(
                tenant_id=tenant_id, object_key=object_key
            )
    finally:
        client.delete_object(Bucket=bucket, Key=object_key)


@pytest.mark.asyncio
async def test_real_cos_multipart_resume_after_interruption_does_not_reupload() -> None:
    """断点续传 + 断网恢复：中断后用 ListParts 发现已传分段，只补缺段并完成。

    模拟：上传首段后「断网/进程中断」（丢弃后续），恢复时以 COS 原生 ListParts
    为可信状态发现已完成分段，跳过重传，补齐剩余段并 complete。断言最终对象完整、
    服务端 crc64 与独立声明一致，且续传阶段不重复上传已完成分段。
    """

    env = _skip_unless_configured()
    bucket = env["TEST_COS_BUCKET"]
    client = create_cos_client(
        region=env["TEST_COS_REGION"],
        secret_id=env["TEST_COS_SECRET_ID"],
        secret_key=env["TEST_COS_SECRET_KEY"],
    )
    verifier = TencentCosMaterialObjectVerifier(bucket=bucket, client=client)
    raw: Any = client  # 分段 API 不在核验 Protocol 上；真实客户端具备这些方法

    tenant_id = uuid4()
    object_key = f"materials/{tenant_id}/{uuid4()}/b04-resume.bin"
    content = bytes((i * 17 + 3) % 256 for i in range(_PART_SIZE * 3))
    declared_crc64 = crc64ecma(content)
    parts_bytes = _split_parts(content)
    assert len(parts_bytes) == 3

    uploaded_part_numbers: list[int] = []

    try:
        created = raw.create_multipart_upload(Bucket=bucket, Key=object_key)
        upload_id = created["UploadId"]

        def _upload(part_number: int, chunk: bytes) -> None:
            raw.upload_part(
                Bucket=bucket,
                Key=object_key,
                Body=chunk,
                PartNumber=part_number,
                UploadId=upload_id,
            )
            uploaded_part_numbers.append(part_number)

        # —— 首次尝试：只成功上传第 1 段就「断网」中断 ——
        _upload(1, parts_bytes[0])
        # （其余段未上传，模拟中断）

        # —— 恢复：以服务端 ListParts 为断点真相 ——
        listed = raw.list_parts(Bucket=bucket, Key=object_key, UploadId=upload_id)
        already = {int(part["PartNumber"]) for part in listed.get("Part", [])}
        assert already == {1}, "服务端应只记录已成功上传的第 1 段"

        resume_uploads_before = len(uploaded_part_numbers)
        for index, chunk in enumerate(parts_bytes, start=1):
            if index in already:
                continue  # 断点续传：跳过已完成分段，不重复上传
            _upload(index, chunk)

        # 续传阶段只补了第 2、3 段，未重传第 1 段。
        resumed = uploaded_part_numbers[resume_uploads_before:]
        assert resumed == [2, 3]
        assert uploaded_part_numbers.count(1) == 1

        # 用最终服务端分段清单 complete（ETag 取自 ListParts，保证与落盘一致）。
        final_listed = raw.list_parts(Bucket=bucket, Key=object_key, UploadId=upload_id)
        parts = [
            {"PartNumber": int(part["PartNumber"]), "ETag": part["ETag"]}
            for part in sorted(
                final_listed["Part"], key=lambda part: int(part["PartNumber"])
            )
        ]
        assert [part["PartNumber"] for part in parts] == [1, 2, 3]
        raw.complete_multipart_upload(
            Bucket=bucket,
            Key=object_key,
            UploadId=upload_id,
            MultipartUpload={"Part": parts},
        )

        stored = await verifier.inspect_uploaded_object(
            tenant_id=tenant_id, object_key=object_key
        )
        assert stored.size_bytes == len(content)
        assert stored.crc64ecma == declared_crc64
    finally:
        client.delete_object(Bucket=bucket, Key=object_key)
