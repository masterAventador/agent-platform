from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_platform.platform.accounts.errors import TokenInvalidOrExpired
from agent_platform.platform.accounts.tokens import (
    AccountTokenPurpose,
    OneTimeToken,
)

NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


def _issue(ttl_seconds: int = 3600) -> OneTimeToken:
    return OneTimeToken.issue(
        user_id=uuid4(),
        purpose=AccountTokenPurpose.PASSWORD_RESET,
        token_digest="digest-xyz",
        ttl_seconds=ttl_seconds,
        now=NOW,
    )


def test_issue_sets_expiry_and_is_valid() -> None:
    token = _issue(ttl_seconds=3600)
    assert token.expires_at == NOW + timedelta(seconds=3600)
    assert token.consumed_at is None
    assert token.is_valid(now=NOW)


def test_expired_token_is_invalid() -> None:
    token = _issue(ttl_seconds=60)
    assert not token.is_valid(now=NOW + timedelta(hours=1))


def test_consume_marks_token_and_prevents_reuse() -> None:
    token = _issue()
    consumed = token.consume(now=NOW + timedelta(minutes=1))
    assert consumed.consumed_at == NOW + timedelta(minutes=1)
    assert not consumed.is_valid(now=NOW + timedelta(minutes=2))
    with pytest.raises(TokenInvalidOrExpired):
        consumed.consume(now=NOW + timedelta(minutes=2))


def test_consume_expired_token_is_rejected() -> None:
    token = _issue(ttl_seconds=60)
    with pytest.raises(TokenInvalidOrExpired):
        token.consume(now=NOW + timedelta(hours=1))
