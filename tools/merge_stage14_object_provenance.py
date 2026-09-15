"""Merge conservative Stage 14 Game/Music object provenance evidence."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import re


OBJECT_TARGETS = {
    "game": ("game_is_paused",),
    "music": ("music_current_id", "music_pause", "music_resume"),
}
TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
TARGET_SOURCE_SHA256 = (
    "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
)
INSTRUCTION_RE = re.compile(
    r"^\s*([0-9a-fA-F]+):\s*(mov|ldr)\s+x0\s*,\s*(.+?)\s*$",
    re.IGNORECASE,
)
REGISTER_LOAD_RE = re.compile(
    r"^\[\s*x(?:[0-9]|[12][0-9]|30|zr)\s*(?:,\s*#[^\]]+)?\]$",
    re.IGNORECASE,
)
LITERAL_LOAD_RE = re.compile(
    r"^\[?\s*(?:0x)?[0-9a-fA-F]+\s*\]?$",
    re.IGNORECASE,
)


def _load_object(path: Path, description: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read {description}: {error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be an object")
    return value


def _normalized_address(value: object) -> str | None:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]+", value):
        return None
    return f"{int(value, 16):x}"


def _x0_instruction(context_before: object) -> tuple[str, str] | None:
    if not isinstance(context_before, list) or not context_before:
        return None
    instruction = context_before[-1]
    if not isinstance(instruction, str):
        return None
    match = INSTRUCTION_RE.fullmatch(instruction)
    if match is None:
        return None
    address, mnemonic, operand = match.groups()
    if mnemonic.lower() == "ldr" and not (
        REGISTER_LOAD_RE.fullmatch(operand) or LITERAL_LOAD_RE.fullmatch(operand)
    ):
        return None
    return f"{int(address, 16):x}", instruction


def _authoritative_references(callsite: dict[str, object], address: str) -> list[dict[str, object]]:
    references = callsite.get("non_call_references")
    if not isinstance(references, list):
        return []
    result = []
    for reference in references:
        if not isinstance(reference, dict):
            continue
        if _normalized_address(reference.get("reference_address")) != address:
            continue
        if reference.get("primary_symbol_source") != "USER_DEFINED":
            continue
        if not isinstance(reference.get("primary_symbol_name"), str) or not reference[
            "primary_symbol_name"
        ]:
            continue
        if reference.get("memory_block_read") is not True:
            continue
        if reference.get("memory_block_execute") is not False:
            continue
        if _normalized_address(reference.get("target_address")) is None:
            continue
        result.append(deepcopy(reference))
    result.sort(
        key=lambda reference: (
            str(reference["reference_address"]),
            str(reference["target_address"]),
            str(reference.get("reference_kind", "")),
        )
    )
    return result


def _classify_object(
    provenance: dict[str, object], object_name: str
) -> dict[str, object]:
    targets = provenance.get("targets")
    if not isinstance(targets, dict):
        raise ValueError("provenance targets must be an object")

    callsites: list[dict[str, object]] = []
    for target_name in OBJECT_TARGETS[object_name]:
        target = targets.get(target_name)
        if not isinstance(target, dict):
            raise ValueError(f"provenance target is missing: {target_name}")
        target_callsites = target.get("callsites")
        if not isinstance(target_callsites, list):
            raise ValueError(f"{target_name}: callsites must be an array")
        for callsite in target_callsites:
            if not isinstance(callsite, dict) or callsite.get("target") != target_name:
                raise ValueError(f"{target_name}: callsite has an invalid target")
            callsites.append(callsite)

    candidate_refs: list[dict[str, object]] = []
    for callsite in callsites:
        instruction = _x0_instruction(callsite.get("context_before"))
        if instruction is None:
            continue
        references = _authoritative_references(callsite, instruction[0])
        candidate_refs.extend(references)

    unique_refs: dict[str, dict[str, object]] = {}
    for reference in candidate_refs:
        key = json.dumps(reference, ensure_ascii=True, sort_keys=True)
        unique_refs[key] = reference
    candidate_refs = [unique_refs[key] for key in sorted(unique_refs)]
    return {
        "status": "unproven",
        "candidate_refs": candidate_refs,
        "this_register": "unproven",
        "return_register": "unproven",
        "source_kind": "candidate_data_reference" if candidate_refs else "unproven",
        "lifetime_evidence": [],
    }


def _validate_version_lock(
    native: dict[str, object], provenance: dict[str, object]
) -> None:
    if native.get("build_id") != TARGET_BUILD_ID:
        raise ValueError("unsupported native build_id")
    if native.get("source_sha256") != TARGET_SOURCE_SHA256:
        raise ValueError("unsupported native source_sha256")
    if provenance.get("build_id") != TARGET_BUILD_ID:
        raise ValueError("provenance build_id does not match native evidence")
    provenance_sha256 = provenance.get("source_sha256")
    if provenance_sha256 is not None and provenance_sha256 != TARGET_SOURCE_SHA256:
        raise ValueError("provenance source_sha256 does not match native evidence")
    functions = native.get("functions")
    targets = provenance.get("targets")
    if not isinstance(functions, dict) or not isinstance(targets, dict):
        raise ValueError("native functions and provenance targets must be objects")
    required_targets = {target for names in OBJECT_TARGETS.values() for target in names}
    required_targets.add("manager_render")
    for key in required_targets:
        function = functions.get(key)
        target = targets.get(key)
        if not isinstance(function, dict) or not isinstance(target, dict):
            raise ValueError(f"version-locked function is missing: {key}")
        if target.get("key") != key:
            raise ValueError(f"{key}: provenance key does not match")
        if target.get("symbol") != function.get("symbol"):
            raise ValueError(f"{key}: provenance symbol does not match")
        if target.get("first_16_bytes") != function.get("first_16_bytes"):
            raise ValueError(f"{key}: provenance entry guard does not match")
        entry_address = _normalized_address(target.get("entry_address"))
        file_offset = function.get("file_offset")
        if not isinstance(file_offset, int) or entry_address != f"{file_offset:x}":
            raise ValueError(f"{key}: provenance entry address does not match")


def merge_object_provenance(
    native_path: Path, provenance_path: Path, output_path: Path
) -> dict[str, object]:
    """Merge only version-locked, explicit object provenance observations."""
    native = _load_object(native_path, "native evidence")
    provenance = _load_object(provenance_path, "provenance evidence")
    _validate_version_lock(native, provenance)

    merged = deepcopy(native)
    merged["objects"] = {
        object_name: _classify_object(provenance, object_name)
        for object_name in ("game", "music")
    }
    blocked_reasons = merged.get("blocked_reasons")
    if not isinstance(blocked_reasons, list) or not all(
        isinstance(reason, str) for reason in blocked_reasons
    ):
        raise ValueError("native blocked_reasons must be a string array")
    blocked_reasons = list(dict.fromkeys(blocked_reasons))
    if "object_ownership_unproven" not in blocked_reasons:
        blocked_reasons.insert(0, "object_ownership_unproven")
    if "render_relay_unproven" not in blocked_reasons:
        blocked_reasons.append("render_relay_unproven")
    merged["blocked_reasons"] = blocked_reasons
    merged["ready_for_runtime_binding"] = False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(merged, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("provenance", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    merge_object_provenance(args.native, args.provenance, args.output)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
