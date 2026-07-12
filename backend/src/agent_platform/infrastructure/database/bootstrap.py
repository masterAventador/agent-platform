from agent_platform.infrastructure.database.models import load_database_models


def initialize_database_metadata() -> None:
    """Register every platform ORM model before a process creates DB resources."""

    load_database_models()
