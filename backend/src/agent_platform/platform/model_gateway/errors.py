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
