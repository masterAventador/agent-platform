import subprocess
import sys


def test_fresh_process_bootstrap_registers_every_foreign_key_target() -> None:
    script = """
from agent_platform.infrastructure.database.base import Base
from agent_platform.infrastructure.database.bootstrap import initialize_database_metadata

initialize_database_metadata()
required_tables = {"tenants", "users", "employees", "runs", "run_commands", "sandbox_leases"}
missing = required_tables.difference(Base.metadata.tables)
raise SystemExit(1 if missing else 0)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
