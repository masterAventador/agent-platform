class ModelGatewayPolicyError(Exception):
    code = "model_gateway_policy_error"


class InvalidModelGatewayPolicy(ModelGatewayPolicyError):
    code = "invalid_model_gateway_policy"


class ModelGatewayPolicyNotFound(ModelGatewayPolicyError):
    code = "model_gateway_policy_not_found"


class ModelGatewayPolicyRevisionConflict(ModelGatewayPolicyError):
    code = "model_gateway_policy_revision_conflict"


class ModelGatewayPolicyPersistenceError(ModelGatewayPolicyError):
    code = "model_gateway_policy_persistence_error"


class CorruptModelGatewayPolicy(ModelGatewayPolicyPersistenceError):
    code = "corrupt_model_gateway_policy"


class InvalidModelGatewayKey(ModelGatewayPolicyError):
    code = "invalid_model_gateway_key"


class ModelGatewayKeyRotationInProgress(ModelGatewayPolicyError):
    code = "model_gateway_key_rotation_in_progress"


class ModelGatewayKeyNotProvisioned(ModelGatewayPolicyError):
    code = "model_gateway_key_not_provisioned"


class ModelGatewayCredentialUnavailable(ModelGatewayPolicyError):
    """当前租户没有可用的可归因网关凭据，且重投不会改变结果（配置性缺陷）。

    消息只含稳定 code，绝不含密钥材料。
    """

    code = "model_gateway_credential_unavailable"

    def __init__(self, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(self.code)


class ModelGatewayCredentialNotReady(ModelGatewayCredentialUnavailable):
    """凭据尚在对账中——秒级自愈的瞬态，必须交队列重投，不得判为永久定义错误。"""

    code = "model_gateway_provisioning_in_progress"


class ModelGatewayProvisioningError(Exception):
    """Provisioner 端口的平台级失败；绝不携带上游响应体或凭据材料。"""

    code = "model_gateway_provisioning_failed"

    def __init__(self, code: str | None = None) -> None:
        if code is not None:
            self.code = code
        super().__init__(self.code)


class ModelGatewayProvisioningTransient(ModelGatewayProvisioningError):
    """网关暂时不可用（传输错误、超时、5xx、限流）；可有界退避重试。"""

    code = "provisioning_transient"


class ModelGatewayProvisioningPermanent(ModelGatewayProvisioningError):
    """网关明确拒绝（4xx、配置非法、凭据错）；重试无意义，立即受控失败。"""

    code = "provisioning_rejected"


class ModelGatewayProvisioningOutcomeUnknown(ModelGatewayProvisioningError):
    """写请求可能已在网关生效但无法确认；禁止自动重放，必须人工介入。"""

    code = "provisioning_outcome_unknown"
