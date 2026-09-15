"""Merge version-locked Stage 14 Game provenance without authorizing runtime use."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re

from tools.stage14_game_provenance import ANCHORS, TARGET_BUILD_ID, TARGET_SOURCE_SHA256


X0_RE = re.compile(r"^\s*([0-9a-fA-F]+):\s*(mov|ldr)\s+x0\s*,\s*(.+?)\s*$", re.I)


def _load(path: Path, description: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be an object")
    return value


def _address(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r"(?:0x)?[0-9a-fA-F]+", value):
        return int(value, 16)
    return None


def _validate_raw_anchors(raw: dict[str, object]) -> None:
    anchors = raw.get("anchors")
    if not isinstance(anchors, dict):
        raise ValueError("raw anchors must be an object")
    for key, (symbol, offset, guard) in ANCHORS.items():
        anchor = anchors.get(key)
        if not isinstance(anchor, dict):
            raise ValueError(f"raw anchor is missing: {key}")
        if anchor.get("symbol") != symbol:
            raise ValueError(f"{key}: symbol does not match")
        if _address(anchor.get("entry_address")) != offset:
            raise ValueError(f"{key}: entry address does not match")
        if anchor.get("first_16_bytes") != guard:
            raise ValueError(f"{key}: entry guard does not match")


def _validate_inputs(native: dict[str, object], raw: dict[str, object]) -> None:
    for description, document in (("native", native), ("raw", raw)):
        if document.get("build_id") != TARGET_BUILD_ID:
            raise ValueError(f"unsupported {description} build_id")
        if document.get("source_sha256") != TARGET_SOURCE_SHA256:
            raise ValueError(f"unsupported {description} source_sha256")
    _validate_raw_anchors(raw)
    if raw.get("schema_version") != "stage14-game-provenance-v2":
        raise ValueError("unsupported raw provenance schema_version")
    functions = native.get("functions")
    if not isinstance(functions, dict):
        raise ValueError("native functions must be an object")
    function = functions.get("game_is_paused")
    symbol, offset, guard = ANCHORS["game_is_paused"]
    if not isinstance(function, dict) or (
        function.get("symbol"), function.get("file_offset"), function.get("first_16_bytes")
    ) != (symbol, offset, guard):
        raise ValueError("native game_is_paused does not match the version lock")

    candidates = raw.get("api_candidates")
    if not isinstance(candidates, list):
        raise ValueError("raw api_candidates must be an array")
    for candidate in candidates:
        if not isinstance(candidate, dict) or not isinstance(candidate.get("xref_context"), dict):
            raise ValueError("api candidate is missing xref_context")
        status = candidate.get("status")
        if status not in {"observed", "corroborated", "proven_for_future_stage"}:
            raise ValueError("api candidate has an invalid status")


def _owner_sources(raw: dict[str, object]) -> list[dict[str, object]]:
    references = raw.get("object_references")
    if not isinstance(references, list):
        raise ValueError("raw object_references must be an array")
    owners = []
    for reference in references:
        if not isinstance(reference, dict) or reference.get("reference_kind") != "READ":
            continue
        if reference.get("memory_block_read") is not True or reference.get("memory_block_execute") is not False:
            continue
        if _address(reference.get("target_address")) is None:
            continue
        owners.append(deepcopy(reference))
    return sorted(owners, key=lambda item: (str(item.get("from")), str(item.get("reference_address"))))


def _target_calls(raw: dict[str, object]) -> tuple[list[dict[str, object]], list[str]]:
    edges = raw.get("edges")
    if not isinstance(edges, list):
        raise ValueError("raw edges must be an array")
    target = ANCHORS["game_is_paused"][1]
    calls: list[dict[str, object]] = []
    x0_chain: list[str] = []
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("edge_kind") != "CALL" or _address(edge.get("to")) != target:
            continue
        call_address = _address(edge.get("call_address"))
        if call_address is None:
            continue
        context = edge.get("context_before")
        if not isinstance(context, list) or not context or not isinstance(context[-1], str):
            continue
        match = X0_RE.fullmatch(context[-1])
        if match is None:
            continue
        if int(match.group(1), 16) + 4 != call_address:
            continue
        object_address = _address(edge.get("object_address"))
        if _address(edge.get("x0_source_address")) is None or object_address != _address(edge.get("x0_source_address")):
            continue
        operand = match.group(3).strip().strip("[]")
        literal = _address(operand)
        if literal != object_address:
            continue
        calls.append(deepcopy(edge))
        x0_chain.append(context[-1])
    return calls, x0_chain


def _lifetime(raw: dict[str, object], owner_address: int | None = None, call_address: int | None = None) -> list[object]:
    events = raw.get("lifetime_events")
    if not isinstance(events, list):
        raise ValueError("raw lifetime_events must be an array")
    if owner_address is None:
        return []
    pair = None
    for ctor_index, ctor_event in enumerate(events):
        if not isinstance(ctor_event, dict) or ctor_event.get("event") != "game_ctor":
            continue
        if _address(ctor_event.get("owner_address")) != owner_address:
            continue
        for init_index in range(ctor_index + 1, len(events)):
            init_event = events[init_index]
            if not isinstance(init_event, dict) or init_event.get("event") != "game_init":
                continue
            if _address(init_event.get("owner_address")) == owner_address:
                pair = (ctor_index, ctor_event, init_index, init_event)
                break
        if pair is not None:
            break
    if pair is None:
        return []
    ctor_index, ctor_event, init_index, init_event = pair
    if init_event.get("update_compatible") is not True or call_address is None:
        return []
    if _address(init_event.get("call_address")) != call_address:
        return []
    for event in events[ctor_index + 1:init_index]:
        if not isinstance(event, dict):
            continue
        if event.get("event") != "game_dtor" or _address(event.get("owner_address")) != owner_address:
            continue
        return []
    return [deepcopy(ctor_event), deepcopy(init_event)]


def _linked_chain(raw: dict[str, object]) -> tuple[dict[str, object], list[str]]:
    """Return whether every required Game* link is present and exact."""
    owner_source = _owner_sources(raw)
    calls, x0_chain = _target_calls(raw)
    owner_keys = {
        (item.get("from"), _address(item.get("target_address")))
        for item in owner_source
    }
    linked_calls = [
        call for call in calls
        if (call.get("owner_from"), _address(call.get("object_address"))) in owner_keys
        and call.get("owner_from") == call.get("from")
    ]
    call = linked_calls[0] if linked_calls else None
    lifetime = _lifetime(raw, _address(call.get("object_address")) if call else None,
                         _address(call.get("call_address")) if call else None)
    owner = next((item for item in owner_source if call and item.get("from") == call.get("owner_from") and _address(item.get("target_address")) == _address(call.get("object_address"))), None)
    chain = {
        "owner_source": [deepcopy(owner)] if owner else [],
        "x0_chain": [call["context_before"][-1]] if call else [],
        "is_paused_calls": [deepcopy(call)] if call else [],
        "lifetime_evidence": lifetime,
    }
    missing = [
        reason for key, reason in (
            ("owner_source", "owner_source_missing"),
            ("x0_chain", "x0_chain_missing"),
            ("is_paused_calls", "is_paused_call_missing"),
            ("lifetime_evidence", "lifetime_evidence_missing"),
        ) if not chain[key]
    ]
    return chain, missing


def game_is_proven(raw: dict[str, object]) -> tuple[bool, list[str]]:
    """Return whether every required Game* link is present and exact."""
    _, missing = _linked_chain(raw)
    return not missing, missing


def merge_game_provenance(native_path: Path, raw_path: Path, output_path: Path) -> dict[str, object]:
    """Merge only complete, version-locked Game* evidence into a new JSON file."""
    native = _load(native_path, "native evidence")
    raw = _load(raw_path, "raw game provenance")
    _validate_inputs(native, raw)
    chain, blocking_links = _linked_chain(raw)
    proven = not blocking_links
    candidates = deepcopy(raw["api_candidates"])
    if any(candidate["status"] == "proven_for_future_stage" for candidate in candidates) and not proven:
        raise ValueError("proven_for_future_stage candidate lacks a complete Game chain")

    blocked_reasons = native.get("blocked_reasons")
    if not isinstance(blocked_reasons, list) or not all(isinstance(item, str) for item in blocked_reasons):
        raise ValueError("native blocked_reasons must be a string array")
    blocked = list(dict.fromkeys(blocked_reasons))
    if proven:
        blocked = [reason for reason in blocked if reason != "object_ownership_unproven"]
    elif "object_ownership_unproven" not in blocked:
        blocked.insert(0, "object_ownership_unproven")
    if "render_relay_unproven" not in blocked:
        blocked.append("render_relay_unproven")

    merged = {
        "build_id": TARGET_BUILD_ID,
        "source_sha256": TARGET_SOURCE_SHA256,
        "game": {
            "status": "proven" if proven else "unproven",
            **chain,
            "blocking_links": blocking_links,
        },
        "api_candidates": candidates,
        "ready_for_runtime_binding": False,
        "blocked_reasons": blocked,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("raw", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    merge_game_provenance(args.native, args.raw, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
