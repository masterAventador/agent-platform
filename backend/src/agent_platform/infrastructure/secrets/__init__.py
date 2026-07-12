"""Credential provider adapters."""

from .local_file import (
    LocalCredentialConfigurationError,
    LocalFileCredentialResolver,
)

__all__ = ["LocalCredentialConfigurationError", "LocalFileCredentialResolver"]
