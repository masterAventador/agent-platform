import json
from pathlib import Path
from typing import Any

from agent_platform.capabilities.social_operations.local_executor_protocol import (
    LocalExecutorMessage,
)
from agent_platform.platform.runs.events import PlatformEvent

REPOSITORY_ROOT = Path(__file__).parents[1]


def main() -> None:
    contract_schemas: dict[Path, dict[str, Any]] = {
        Path("contracts/events/platform-event.schema.json"): PlatformEvent.model_json_schema(),
        Path(
            "contracts/capabilities/social-operations/local-executor-v1.schema.json"
        ): LocalExecutorMessage.model_json_schema(),
    }
    for relative_path, schema in contract_schemas.items():
        output_path = REPOSITORY_ROOT / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                schema,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
