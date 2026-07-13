class ToolExecutionError(RuntimeError):
    """A sanitized error raised when a tool adapter or credential provider fails."""


class ToolInvocationClaimRejected(RuntimeError):
    """The run was cancelled or terminal before the invocation could be claimed."""
