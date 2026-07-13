class KnowledgeProviderUnavailable(Exception):
    """知识供应商当前无法完成请求。"""


class InvalidKnowledgeProviderResponse(Exception):
    """知识供应商返回了无法被平台安全解释的响应。"""
