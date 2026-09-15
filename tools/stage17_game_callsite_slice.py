"""Lock the four Stage 17 Game lifecycle PLT callsites."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tools.stage14_game_provenance import TARGET_BUILD_ID, TARGET_SOURCE_SHA256


CALLSITES = {
    "manager_init_game_ctor": ("game_ctor", "Init@003f5898", "003f5a30"),
    "manager_init_game_init": ("game_init", "Init@003f5898", "003f5a44"),
    "manager_update_game_update": ("game_update", "Update@003f8db8", "003f905c"),
    "shutdown_game_dtor": ("game_dtor", "IsaacShutdown@003b38d0", "003b3978"),
}


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read Stage 16 PLT dataflow: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("Stage 16 PLT dataflow root must be an object")
    return value


def write_callsite_targets(plt_path: Path, output_path: Path) -> dict[str, object]:
    """Write only the four verified Stage 17 lifecycle callsite records."""
    source = _load(plt_path)
    if source.get("schema_version") != "stage16-game-plt-dataflow-v1":
        raise ValueError("unsupported Stage 16 PLT dataflow schema")
    if source.get("build_id") != TARGET_BUILD_ID or source.get("source_sha256") != TARGET_SOURCE_SHA256:
        raise ValueError("unsupported Stage 16 PLT dataflow identity")
    calls = source.get("indirect_calls")
    if not isinstance(calls, list):
        raise ValueError("Stage 16 indirect_calls must be an array")

    result: dict[str, object] = {}
    for key, expected in CALLSITES.items():
        matches = [
            item for item in calls
            if isinstance(item, dict)
            and (item.get("target"), item.get("caller"), item.get("call_address")) == expected
        ]
        if len(matches) != 1:
            raise ValueError(f"{key}: required Stage 16 PLT callsite is missing or ambiguous")
        result[key] = matches[0]
    document: dict[str, object] = {
        "schema_version": "stage17-game-callsite-targets-v1",
        "build_id": TARGET_BUILD_ID,
        "source_sha256": TARGET_SOURCE_SHA256,
        "callsites": result,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(document, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plt_dataflow", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_callsite_targets(args.plt_dataflow, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
