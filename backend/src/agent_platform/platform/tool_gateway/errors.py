class ToolExecutionError(RuntimeError):
    """A sanitized error raised when a tool adapter or credential provider fails."""

    def __init__(self, code: str = "tool_execution_failed") -> None:
        super().__init__(code)
        self.code = code


class ToolExecutionFailure(RuntimeError):
    """Typed, sanitized executor failure carrying a stable error code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ToolInvocationClaimRejected(RuntimeError):
    """The run was cancelled or terminal before the invocation could be claimed."""
