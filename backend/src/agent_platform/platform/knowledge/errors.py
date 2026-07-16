class KnowledgeProviderNotConfigured(Exception):
    """知识库引用的供应商未在当前部署注册；属于部署配置缺陷，重试无法恢复。"""


class KnowledgeProviderUnavailable(Exception):
    """知识供应商暂时无法完成请求（网络故障、超时或服务端错误），可重试。"""


class KnowledgeProviderRequestRejected(Exception):
    """知识供应商明确拒绝了请求（认证失败、权限不足、资源不存在或业务错误码），重试无法恢复。

    消息只允许携带脱敏后的稳定原因（如 HTTP 状态码或业务错误码），不得包含原始响应内容。
    """


class InvalidKnowledgeProviderResponse(Exception):
    """知识供应商返回了无法被平台安全解释的响应。"""
