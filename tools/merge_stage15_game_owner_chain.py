"""Conservatively classify version-locked Stage 15 Game owner-chain evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from tools.stage14_game_provenance import TARGET_BUILD_ID, TARGET_SOURCE_SHA256
from tools.stage15_game_owner_chain import OWNER_CHAIN_ANCHORS


ADDRESS_RE = re.compile(r"(?:0x)?[0-9a-fA-F]+$")


def _load(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read owner-chain evidence: {error}") from error
    if not isinstance(value, dict):
        raise ValueError("owner-chain evidence root must be an object")
    return value


def _address(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and ADDRESS_RE.fullmatch(value):
        return int(value, 16)
    return None


def _validate_anchors(raw: dict[str, object]) -> None:
    anchors = raw.get("anchors")
    if not isinstance(anchors, dict):
        raise ValueError("owner-chain anchors must be an object")
    for key, (symbol, offset, guard) in OWNER_CHAIN_ANCHORS.items():
        anchor = anchors.get(key)
        if not isinstance(anchor, dict):
            raise ValueError(f"owner-chain anchor is missing: {key}")
        if (
            anchor.get("symbol") != symbol
            or _address(anchor.get("entry_address")) != offset
            or anchor.get("first_16_bytes") != guard
        ):
            raise ValueError(f"owner-chain anchor does not match: {key}")


def _validate(raw: dict[str, object]) -> None:
    if raw.get("schema_version") != "stage15-game-owner-chain-v1":
        raise ValueError("unsupported owner-chain schema_version")
    if raw.get("build_id") != TARGET_BUILD_ID:
        raise ValueError("unsupported owner-chain build_id")
    if raw.get("source_sha256") != TARGET_SOURCE_SHA256:
        raise ValueError("unsupported owner-chain source_sha256")
    _validate_anchors(raw)
    for key in (
        "constructor_callers",
        "destructor_callers",
        "caller_parents",
        "owner_writes",
        "update_reads",
        "lifetime_observations",
    ):
        if not isinstance(raw.get(key), list):
            raise ValueError(f"owner-chain {key} must be an array")


def _owner_publishes(raw: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for item in raw["owner_writes"]:
        if not isinstance(item, dict):
            continue
        if (
            item.get("memory_block_read") is not True
            or item.get("memory_block_execute") is not False
            or item.get("published") is not True
            or _address(item.get("owner_slot")) is None
            or _address(item.get("object_address")) is None
        ):
            continue
        result.append(deepcopy(item))
    return result


def _update_reads(raw: dict[str, object]) -> list[dict[str, object]]:
    result = []
    for item in raw["update_reads"]:
        if not isinstance(item, dict):
            continue
        instruction = item.get("instruction")
        if (
            item.get("memory_block_read") is not True
            or item.get("memory_block_execute") is not False
            or item.get("explicit_x0") is not True
            or item.get("x0_contiguous") is not True
            or not isinstance(instruction, str)
            or "ldr x0" not in instruction.lower()
            or _address(item.get("owner_slot")) is None
            or _address(item.get("object_address")) is None
        ):
            continue
        result.append(deepcopy(item))
    return result


def _lifetime_pair(
    raw: dict[str, object], owner_slot: int | None, object_address: int | None
) -> list[dict[str, object]]:
    if owner_slot is None or object_address is None:
        return []
    relevant = [
        item
        for item in raw["lifetime_observations"]
        if isinstance(item, dict)
        and _address(item.get("owner_slot")) == owner_slot
        and _address(item.get("object_address")) == object_address
    ]
    ctor = next(
        (
            item
            for item in relevant
            if item.get("event") == "game_ctor" and item.get("published") is True
        ),
        None,
    )
    update = next(
        (
            item
            for item in relevant
            if item.get("event") == "manager_update_read"
            and item.get("update_compatible") is True
        ),
        None,
    )
    if ctor is None or update is None:
        return []
    if any(item.get("event") == "game_dtor" for item in relevant):
        return []
    return [deepcopy(ctor), deepcopy(update)]


def _linked_owner_chain(raw: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    publishes = _owner_publishes(raw)
    reads = _update_reads(raw)
    missing: list[str] = []
    if not publishes:
        missing.append("owner_publish_missing")
    if not reads:
        missing.append("update_read_missing")

    matching_slot = [
        (publish, read)
        for publish in publishes
        for read in reads
        if _address(publish.get("owner_slot")) == _address(read.get("owner_slot"))
    ]
    if publishes and reads and not matching_slot:
        missing.append("owner_update_slot_mismatch")
    matching_object = [
        (publish, read)
        for publish, read in matching_slot
        if _address(publish.get("object_address")) == _address(read.get("object_address"))
    ]
    if matching_slot and not matching_object:
        missing.append("owner_update_object_mismatch")

    publish: dict[str, object] | None = None
    read: dict[str, object] | None = None
    lifetime: list[dict[str, object]] = []
    if matching_object:
        publish, read = matching_object[0]
        lifetime = _lifetime_pair(
            raw,
            _address(publish.get("owner_slot")),
            _address(publish.get("object_address")),
        )
        if not lifetime:
            missing.append("lifetime_pair_missing")
    elif publishes and reads:
        missing.append("x0_chain_missing")

    chain = {
        "owner_slot": deepcopy(publish) if publish else [],
        "constructor_publish": deepcopy(publish) if publish else [],
        "update_read": deepcopy(read) if read else [],
        "x0_chain": [read["instruction"]] if read else [],
        "lifetime_pair": lifetime,
    }
    return chain, missing


def merge_owner_chain(raw_path: Path, output_path: Path) -> dict[str, object]:
    """Write a conservative, version-locked Game owner-chain classification."""
    raw = _load(raw_path)
    _validate(raw)
    chain, missing = _linked_owner_chain(raw)
    merged = {
        "build_id": TARGET_BUILD_ID,
        "source_sha256": TARGET_SOURCE_SHA256,
        "game_owner_chain": {
            "status": "owner_chain_proven" if not missing else "unproven",
            **chain,
            "blocking_links": missing,
        },
        "ready_for_runtime_binding": False,
        "blocked_reasons": [
            *( ["object_ownership_unproven"] if missing else [] ),
            "render_relay_unproven",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    merge_owner_chain(args.raw, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
