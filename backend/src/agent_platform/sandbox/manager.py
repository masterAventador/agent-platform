from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from agent_platform.sandbox.entities import SandboxLease, SandboxLeaseStatus, SandboxScope
from agent_platform.sandbox.errors import (
    SandboxLeaseBusy,
    SandboxLeaseNotFound,
    SandboxLeaseUnavailable,
    SandboxProviderNotConfigured,
)
from agent_platform.sandbox.ports import (
    ProviderSandbox,
    RunExecutionEnvironment,
    SandboxAcquireRequest,
    SandboxBackendValidator,
    SandboxLeaseUnitOfWorkFactory,
    SandboxProvider,
    SandboxProviderRegistry,
)

logger = logging.getLogger(__name__)


class SandboxManager:
    """管理 run-scoped 沙盒租约；每次外部调用前后独立提交租约状态。"""

    def __init__(
        self,
        *,
        unit_of_work_factory: SandboxLeaseUnitOfWorkFactory,
        providers: SandboxProviderRegistry,
        provider_name: str,
        backend_validator: SandboxBackendValidator,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._providers = providers
        self._provider_name = provider_name
        self._backend_validator = backend_validator

    async def acquire(
        self,
        *,
        scope: SandboxScope,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> RunExecutionEnvironment:
        timestamp = self._now(now)
        provider = self._provider(self._provider_name)
        action = "provision"
        async with self._unit_of_work_factory() as unit_of_work:
            lease = await unit_of_work.leases.get_by_scope(
                scope=scope, provider=self._provider_name
            )
            if lease is None:
                lease = SandboxLease.create(
                    scope=scope,
                    provider=self._provider_name,
                    ttl=ttl,
                    now=timestamp,
                )
                await unit_of_work.leases.add(lease)
                await unit_of_work.commit()
            elif lease.status in {
                SandboxLeaseStatus.PROVISIONING,
                SandboxLeaseStatus.DELETING,
            }:
                if lease.expires_at > timestamp:
                    raise SandboxLeaseBusy
                if lease.status is SandboxLeaseStatus.DELETING and lease.sandbox_id is not None:
                    action = "delete_then_provision"
                else:
                    lease = lease.begin_provisioning(ttl=ttl, now=timestamp)
                    await unit_of_work.leases.update(lease)
                    await unit_of_work.commit()
            elif lease.expires_at > timestamp and (
                lease.status is SandboxLeaseStatus.ACTIVE
                or (lease.status is SandboxLeaseStatus.ERROR and lease.sandbox_id is not None)
            ):
                action = "reconnect"
            elif lease.sandbox_id is not None:
                lease = lease.begin_delete(now=timestamp)
                await unit_of_work.leases.update(lease)
                await unit_of_work.commit()
                action = "delete_then_provision"
            else:
                lease = lease.begin_provisioning(ttl=ttl, now=timestamp)
                await unit_of_work.leases.update(lease)
                await unit_of_work.commit()

        if action == "reconnect":
            return await self._reconnect_provider(lease=lease, provider=provider)
        if action == "delete_then_provision":
            try:
                await provider.delete(
                    sandbox_id=self._required_sandbox_id(lease),
                    lease_id=lease.id,
                    sandbox_epoch=lease.sandbox_epoch,
                )
            except Exception:
                failed = lease.mark_error("delete_before_reacquire_failed", now=timestamp)
                await self._persist_if_current(
                    failed,
                    expected_status=lease.status,
                    expected_epoch=lease.epoch,
                )
                raise
            provisioning = lease.begin_provisioning(ttl=ttl, now=timestamp)
            if not await self._persist_if_current(
                provisioning,
                expected_status=lease.status,
                expected_epoch=lease.epoch,
            ):
                raise SandboxLeaseUnavailable
            lease = provisioning
        return await self._provision(lease=lease, scope=scope, provider=provider, now=timestamp)

    async def reconnect(self, *, lease_id: UUID, scope: SandboxScope) -> RunExecutionEnvironment:
        lease = await self._lease_in_scope(lease_id=lease_id, scope=scope)
        if lease.status is not SandboxLeaseStatus.ACTIVE or lease.sandbox_id is None:
            raise SandboxLeaseUnavailable
        return await self._reconnect_provider(
            lease=lease,
            provider=self._provider(lease.provider),
        )

    async def reconnect_active(
        self,
        *,
        scope: SandboxScope,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> RunExecutionEnvironment:
        timestamp = self._now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            lease = await unit_of_work.leases.get_by_scope(
                scope=scope,
                provider=self._provider_name,
            )
            if lease is None:
                raise SandboxLeaseNotFound
            if lease.sandbox_id is None or lease.status not in {
                SandboxLeaseStatus.ACTIVE,
                SandboxLeaseStatus.ERROR,
            }:
                raise SandboxLeaseUnavailable
            claimed = (
                lease.activate(lease.sandbox_id, now=timestamp)
                if lease.status is SandboxLeaseStatus.ERROR
                else lease
            ).renew(ttl=ttl, now=timestamp)
            await unit_of_work.leases.update(claimed)
            await unit_of_work.commit()
        return await self._reconnect_provider(
            lease=claimed,
            provider=self._provider(claimed.provider),
        )

    async def delete(self, *, lease_id: UUID, scope: SandboxScope) -> SandboxLease:
        async with self._unit_of_work_factory() as unit_of_work:
            lease = await unit_of_work.leases.get(tenant_id=scope.tenant_id, lease_id=lease_id)
            if lease is None or lease.scope != scope:
                raise SandboxLeaseNotFound
            if lease.status in {SandboxLeaseStatus.DELETED, SandboxLeaseStatus.EXPIRED}:
                return lease
            if lease.status in {
                SandboxLeaseStatus.PROVISIONING,
                SandboxLeaseStatus.DELETING,
            }:
                raise SandboxLeaseBusy
            if lease.sandbox_id is None:
                deleted = lease.mark_deleted()
                await unit_of_work.leases.update(deleted)
                await unit_of_work.commit()
                return deleted
            provider = self._provider(lease.provider)
            deleting = lease.begin_delete()
            await unit_of_work.leases.update(deleting)
            await unit_of_work.commit()
        try:
            await provider.delete(
                sandbox_id=self._required_sandbox_id(lease),
                lease_id=lease.id,
                sandbox_epoch=lease.sandbox_epoch,
            )
        except Exception:
            await self._persist_if_current(
                deleting.mark_error("delete_failed"),
                expected_status=SandboxLeaseStatus.DELETING,
                expected_epoch=deleting.epoch,
            )
            raise
        deleted = deleting.mark_deleted()
        if not await self._persist_if_current(
            deleted,
            expected_status=SandboxLeaseStatus.DELETING,
            expected_epoch=deleting.epoch,
        ):
            raise SandboxLeaseUnavailable
        return deleted

    async def renew(
        self,
        *,
        lease_id: UUID,
        scope: SandboxScope,
        ttl: timedelta,
        now: datetime | None = None,
    ) -> SandboxLease:
        async with self._unit_of_work_factory() as unit_of_work:
            lease = await unit_of_work.leases.get(tenant_id=scope.tenant_id, lease_id=lease_id)
            if lease is None or lease.scope != scope:
                raise SandboxLeaseNotFound
            if lease.status is not SandboxLeaseStatus.ACTIVE:
                raise SandboxLeaseUnavailable
            renewed = lease.renew(ttl=ttl, now=now)
            await unit_of_work.leases.update(renewed)
            await unit_of_work.commit()
            return renewed

    async def cleanup_expired(self, *, now: datetime | None = None, limit: int = 100) -> list[UUID]:
        if limit <= 0:
            raise ValueError("limit 必须大于零")
        timestamp = self._now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            leases = await unit_of_work.leases.list_expired(now=timestamp, limit=limit)
            claimed = [lease.begin_delete(now=timestamp) for lease in leases]
            for deleting in claimed:
                await unit_of_work.leases.update(deleting)
            await unit_of_work.commit()
        cleaned: list[UUID] = []
        for lease, deleting in zip(leases, claimed, strict=True):
            try:
                if lease.sandbox_id is not None:
                    await self._provider(lease.provider).delete(
                        sandbox_id=lease.sandbox_id,
                        lease_id=lease.id,
                        sandbox_epoch=lease.sandbox_epoch,
                    )
                else:
                    provider = self._provider(lease.provider)
                    await provider.delete_by_lease(
                        lease_id=lease.id,
                        sandbox_epoch=lease.sandbox_epoch,
                    )
            except Exception:
                await self._persist_if_current(
                    deleting.mark_error("cleanup_failed", now=timestamp),
                    expected_status=SandboxLeaseStatus.DELETING,
                    expected_epoch=deleting.epoch,
                )
                continue
            expired = deleting.mark_expired(now=timestamp)
            if await self._persist_if_current(
                expired,
                expected_status=SandboxLeaseStatus.DELETING,
                expected_epoch=deleting.epoch,
            ):
                cleaned.append(lease.id)
        return cleaned

    async def _provision(
        self,
        *,
        lease: SandboxLease,
        scope: SandboxScope,
        provider: SandboxProvider,
        now: datetime,
    ) -> RunExecutionEnvironment:
        request = SandboxAcquireRequest(
            lease_id=lease.id,
            scope=scope,
            expires_at=lease.expires_at,
            sandbox_epoch=lease.sandbox_epoch,
        )
        provisioned: ProviderSandbox | None = None
        try:
            provisioned = await provider.acquire(request)
            self._validate_generation(provisioned, expected=lease.sandbox_epoch)
        except Exception:
            if provisioned is not None:
                await self._delete_or_disconnect_generation(
                    provider,
                    lease_id=lease.id,
                    provisioned=provisioned,
                )
            await self._persist_if_current(
                lease.mark_error("acquire_failed", now=now),
                expected_status=SandboxLeaseStatus.PROVISIONING,
                expected_epoch=lease.epoch,
            )
            raise
        try:
            self._backend_validator.validate(provisioned.backend)
        except Exception:
            error_code = "backend_validation_failed"
            failed = lease
            try:
                await provider.delete(
                    sandbox_id=provisioned.sandbox_id,
                    lease_id=lease.id,
                    sandbox_epoch=lease.sandbox_epoch,
                )
            except Exception:
                error_code = "backend_validation_cleanup_failed"
                failed = lease.activate(provisioned.sandbox_id, now=now)
            await self._persist_if_current(
                failed.mark_error(error_code, now=now),
                expected_status=SandboxLeaseStatus.PROVISIONING,
                expected_epoch=lease.epoch,
            )
            raise
        active = lease.activate(provisioned.sandbox_id, now=now)
        activated = await self._persist_if_current(
            active,
            expected_status=SandboxLeaseStatus.PROVISIONING,
            expected_epoch=lease.epoch,
        )
        if not activated:
            try:
                await provider.delete(
                    sandbox_id=provisioned.sandbox_id,
                    lease_id=lease.id,
                    sandbox_epoch=lease.sandbox_epoch,
                )
            except Exception:
                await self._disconnect_provider(provider, sandbox_id=provisioned.sandbox_id)
                await self._mark_current_error(
                    tenant_id=lease.tenant_id,
                    lease_id=lease.id,
                    code="late_acquire_cleanup_failed",
                    now=now,
                    origin_epoch=lease.epoch,
                )
            raise SandboxLeaseUnavailable
        return self._environment(active, provisioned)

    async def _lease_in_scope(self, *, lease_id: UUID, scope: SandboxScope) -> SandboxLease:
        async with self._unit_of_work_factory() as unit_of_work:
            lease = await unit_of_work.leases.get(tenant_id=scope.tenant_id, lease_id=lease_id)
        if lease is None or lease.scope != scope:
            raise SandboxLeaseNotFound
        return lease

    async def _reconnect_provider(
        self, *, lease: SandboxLease, provider: SandboxProvider
    ) -> RunExecutionEnvironment:
        sandbox_id = self._required_sandbox_id(lease)
        provisioned: ProviderSandbox | None = None
        try:
            provisioned = await provider.reconnect(
                sandbox_id=sandbox_id,
                lease_id=lease.id,
                sandbox_epoch=lease.sandbox_epoch,
            )
            self._validate_generation(provisioned, expected=lease.sandbox_epoch)
            self._backend_validator.validate(provisioned.backend)
            if provisioned.sandbox_id != sandbox_id:
                raise ValueError("供应商重连返回了不同的 sandbox_id")
        except Exception:
            if provisioned is not None:
                await self._disconnect_provider(provider, sandbox_id=provisioned.sandbox_id)
            await self._persist_if_current(
                lease.mark_error("reconnect_failed"),
                expected_status=lease.status,
                expected_epoch=lease.epoch,
            )
            raise
        recovered = lease
        if lease.status is SandboxLeaseStatus.ERROR:
            recovered = lease.activate(sandbox_id)
        if not await self._persist_if_current(
            recovered,
            expected_status=lease.status,
            expected_epoch=lease.epoch,
        ):
            await self._disconnect_provider(provider, sandbox_id=provisioned.sandbox_id)
            raise SandboxLeaseUnavailable
        return self._environment(recovered, provisioned)

    async def _persist_if_current(
        self,
        lease: SandboxLease,
        *,
        expected_status: SandboxLeaseStatus,
        expected_epoch: int,
    ) -> bool:
        async with self._unit_of_work_factory() as unit_of_work:
            updated = await unit_of_work.leases.update_if_current(
                lease,
                expected_status=expected_status,
                expected_epoch=expected_epoch,
            )
            await unit_of_work.commit()
            return updated

    async def _mark_current_error(
        self,
        *,
        tenant_id: UUID,
        lease_id: UUID,
        code: str,
        now: datetime,
        origin_epoch: int,
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            current = await unit_of_work.leases.get(tenant_id=tenant_id, lease_id=lease_id)
            if (
                current is None
                or current.status
                not in {
                    SandboxLeaseStatus.DELETING,
                    SandboxLeaseStatus.EXPIRED,
                    SandboxLeaseStatus.ERROR,
                }
                or current.epoch not in {origin_epoch + 1, origin_epoch + 2}
            ):
                return
            await unit_of_work.leases.update_if_current(
                current.mark_error(code, now=now),
                expected_status=current.status,
                expected_epoch=current.epoch,
            )
            await unit_of_work.commit()

    @staticmethod
    async def _disconnect_provider(provider: SandboxProvider, *, sandbox_id: str) -> None:
        try:
            await provider.disconnect(sandbox_id=sandbox_id)
        except Exception as error:
            logger.error(
                "sandbox_provider_disconnect_failed",
                extra={"error_type": type(error).__name__},
            )

    @classmethod
    async def _delete_or_disconnect_generation(
        cls,
        provider: SandboxProvider,
        *,
        lease_id: UUID,
        provisioned: ProviderSandbox,
    ) -> None:
        try:
            await provider.delete(
                sandbox_id=provisioned.sandbox_id,
                lease_id=lease_id,
                sandbox_epoch=provisioned.sandbox_epoch,
            )
        except Exception:
            await cls._disconnect_provider(
                provider,
                sandbox_id=provisioned.sandbox_id,
            )

    def _provider(self, name: str) -> SandboxProvider:
        provider = self._providers.get(name)
        if provider is None:
            raise SandboxProviderNotConfigured(name)
        return provider

    @staticmethod
    def _required_sandbox_id(lease: SandboxLease) -> str:
        if lease.sandbox_id is None:
            raise SandboxLeaseUnavailable
        return lease.sandbox_id

    @staticmethod
    def _validate_generation(provisioned: ProviderSandbox, *, expected: int) -> None:
        if provisioned.sandbox_epoch != expected:
            raise ValueError("供应商返回了不同的 sandbox generation")

    @staticmethod
    def _environment(lease: SandboxLease, provisioned: ProviderSandbox) -> RunExecutionEnvironment:
        return RunExecutionEnvironment(
            lease=lease,
            workspace=provisioned.workspace,
            backend=provisioned.backend,
        )

    @staticmethod
    def _now(value: datetime | None) -> datetime:
        if value is None:
            return datetime.now(UTC)
        if value.tzinfo is None:
            raise ValueError("时间必须包含时区")
        return value.astimezone(UTC)
