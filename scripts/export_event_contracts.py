import json
from pathlib import Path

from agent_platform.platform.runs.events import PlatformEvent

REPOSITORY_ROOT = Path(__file__).parents[1]


def main() -> None:
    output_path = REPOSITORY_ROOT / "contracts/events/platform-event.schema.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(
            PlatformEvent.model_json_schema(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
