class McpServerNotFound(Exception):
    pass


class ToolNotFound(Exception):
    pass


class ToolVersionNotFound(Exception):
    pass


class RegistryNameAlreadyExists(Exception):
    pass


class InvalidApprovalPolicy(Exception):
    pass


class InvalidToolSchema(Exception):
    pass


class ToolInUse(Exception):
    def __init__(self, references: list[object] | None = None) -> None:
        super().__init__("tool is referenced by employees")
        self.references = references or []


class McpServerInUse(Exception):
    def __init__(self, references: list[object] | None = None) -> None:
        super().__init__("mcp server tools are referenced by employees")
        self.references = references or []


class McpConnectionFailed(Exception):
    """Stable sanitized MCP connectivity failure; never carries upstream payloads."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
