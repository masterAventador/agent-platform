class MCPClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class MCPTimeoutError(MCPClientError):
    def __init__(self) -> None:
        super().__init__("mcp_timeout", "MCP request timed out")


class MCPRemoteError(MCPClientError):
    def __init__(self) -> None:
        super().__init__("mcp_remote_error", "MCP server request failed")


class MCPToolExecutionError(MCPClientError):
    def __init__(self) -> None:
        super().__init__("mcp_tool_execution_failed", "MCP tool execution failed")
