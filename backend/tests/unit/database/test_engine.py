from agent_platform.infrastructure.database.engine import create_database_engine


def test_database_engine_pre_pings_connections_before_pool_reuse() -> None:
    engine = create_database_engine("sqlite+aiosqlite:///:memory:")

    assert engine.pool._pre_ping is True  # noqa: SLF001
