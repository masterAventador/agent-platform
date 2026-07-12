class SandboxError(Exception):
    """Sandbox Manager 的稳定错误基类。"""


class SandboxProviderNotConfigured(SandboxError):
    """平台配置引用了未装配的沙盒供应商。"""


class SandboxLeaseNotFound(SandboxError):
    """可信身份作用域内不存在指定租约。"""


class SandboxLeaseBusy(SandboxError):
    """租约正在创建或删除，不能并发执行另一项生命周期操作。"""


class SandboxLeaseScopeConflict(SandboxError):
    """隔离作用域或供应商 sandbox_id 已被另一租约占用。"""


class SandboxLeaseUnavailable(SandboxError):
    """租约当前不能重连。"""
