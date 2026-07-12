from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class MCPStreamableHTTPConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["streamable_http"] = "streamable_http"
    url: str
    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    timeout_seconds: float = Field(default=30.0, gt=0)


class MCPStdioConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    transport: Literal["stdio"] = "stdio"
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = Field(default=None, repr=False)
    timeout_seconds: float = Field(default=30.0, gt=0)


type MCPServerConfig = MCPStreamableHTTPConfig | MCPStdioConfig


class MCPTool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    title: str | None = None
    description: str | None = None
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue] | None = None
