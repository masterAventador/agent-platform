from __future__ import annotations

from agent_platform.capabilities.registry import (
    CapabilityConflictError,
    CapabilityHost,
    CapabilityHostError,
    CapabilityNotInstalledError,
    DuplicateCapabilityError,
    UnsatisfiedCoreProtocolError,
)

__all__ = [
    "CapabilityConflictError",
    "CapabilityHostError",
    "CapabilityNotInstalledError",
    "DuplicateCapabilityError",
    "MockCapabilityHost",
    "UnsatisfiedCoreProtocolError",
]


class MockCapabilityHost(CapabilityHost):
    """Isolated in-memory host for capability tests; shares production validation."""
