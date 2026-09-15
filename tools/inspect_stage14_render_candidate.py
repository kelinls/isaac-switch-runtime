"""Inspect one version-locked Stage 14 Render relay candidate without patching it."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.find_stage14_render_caves import (
    FALLBACK_DELTA,
    RELAY_LENGTH,
    RESERVED_INTERVALS,
    SLOT_DELTA,
    encode_branch,
    find_render_caves,
    is_branch_reachable,
)


TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
TARGET_SOURCE_SHA256 = (
    "cc363208c220f65547edda6d8268b3b6c0f7373b14c91ae6ffb378018de4b66a"
)
TARGET_TEXT_START = 0x0
TARGET_TEXT_END = 0x68D000
TARGET_RENDER_OFFSET = 0x3F9684
TARGET_RENDER_GUARD = "FFC302D1E83B00FDFD7B08A9FD030291"
TARGET_RENDER_CANDIDATE_COUNT = 234
FALLBACK_LENGTH = SLOT_DELTA - FALLBACK_DELTA
SLOT_LENGTH = RELAY_LENGTH - SLOT_DELTA
SURROUNDING_BEFORE = 0x20
SURROUNDING_AFTER = 0x40


def _load_evidence(path: Path) -> dict[str, object]:
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read Stage 14 evidence: {error}") from error
    if not isinstance(evidence, dict):
        raise ValueError("Stage 14 evidence root must be an object")
    return evidence


def _require_int(value: object, description: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{description} must be an integer")
    return value


def _overlaps(left: range, right: range) -> bool:
    return left.start < right.stop and right.start < left.stop


def inspect_render_candidate(
    nro_path: Path, candidate_offset: int, evidence_path: Path
) -> dict[str, object]:
    """Return mechanical evidence for one candidate, never a safety approval."""
    candidate_offset = _require_int(candidate_offset, "candidate offset")
    if candidate_offset % 4:
        raise ValueError("candidate offset must be 4-byte aligned")

    try:
        data = nro_path.read_bytes()
    except OSError as error:
        raise ValueError(f"cannot read NRO: {error}") from error
    if len(data) < 0x54 or data[0x10:0x14] != b"NRO0":
        raise ValueError("NRO0 header not found")

    evidence = _load_evidence(evidence_path)
    build_id = data[0x40:0x54].hex().upper()
    if build_id != TARGET_BUILD_ID or evidence.get("build_id") != build_id:
        raise ValueError("unsupported or mismatched Stage 14 build_id")
    source_sha256 = hashlib.sha256(data).hexdigest()
    if source_sha256 != TARGET_SOURCE_SHA256:
        raise ValueError("unsupported Stage 14 NRO source_sha256")
    if evidence.get("source_sha256") != TARGET_SOURCE_SHA256:
        raise ValueError("Stage 14 evidence source_sha256 is not version-locked")

    text_start = int.from_bytes(data[0x20:0x24], "little")
    text_size = int.from_bytes(data[0x24:0x28], "little")
    text_end = text_start + text_size
    if (
        text_start % 4
        or text_size < RELAY_LENGTH
        or text_start > len(data)
        or text_end > len(data)
    ):
        raise ValueError("NRO text section is outside the file")
    if text_start != TARGET_TEXT_START or text_end != TARGET_TEXT_END:
        raise ValueError("NRO text section does not match the Stage 14 target")
    candidate_end = candidate_offset + RELAY_LENGTH
    if (
        candidate_offset < text_start
        or candidate_offset > len(data)
        or candidate_end > text_end
    ):
        raise ValueError("candidate is outside declared NRO text")

    candidate_interval = range(candidate_offset, candidate_end)
    overlapping_reserved = [
        reserved for reserved in RESERVED_INTERVALS
        if _overlaps(candidate_interval, reserved)
    ]
    if overlapping_reserved:
        raise ValueError("candidate overlaps a reserved interval")

    functions = evidence.get("functions")
    if not isinstance(functions, dict):
        raise ValueError("Stage 14 evidence functions must be an object")
    manager_render = functions.get("manager_render")
    if not isinstance(manager_render, dict):
        raise ValueError("manager_render evidence is missing")
    render_offset = _require_int(
        manager_render.get("file_offset"), "manager_render offset"
    )
    expected_entry_guard = manager_render.get("first_16_bytes")
    if not isinstance(expected_entry_guard, str):
        raise ValueError("manager_render entry guard must be a hex string")
    if render_offset != TARGET_RENDER_OFFSET:
        raise ValueError("manager_render offset is not version-locked")
    if expected_entry_guard != TARGET_RENDER_GUARD:
        raise ValueError("manager_render entry guard is not version-locked")
    entry_end = render_offset + 16
    if render_offset < text_start or entry_end > text_end:
        raise ValueError("manager_render entry guard is outside declared NRO text")
    entry_guard = data[render_offset:entry_end].hex().upper()
    if entry_guard != TARGET_RENDER_GUARD:
        raise ValueError("manager_render entry guard does not match NRO bytes")

    candidates = evidence.get("render_candidates")
    if not isinstance(candidates, list):
        raise ValueError("Stage 14 evidence render_candidates must be an array")
    recomputed_candidates = find_render_caves(
        data,
        TARGET_RENDER_OFFSET,
        reserved=RESERVED_INTERVALS,
    )
    if len(recomputed_candidates) != TARGET_RENDER_CANDIDATE_COUNT:
        raise ValueError("NRO does not contain the fixed 234 Render candidates")
    if len(candidates) != TARGET_RENDER_CANDIDATE_COUNT:
        raise ValueError("Stage 14 evidence must contain all 234 Render candidates")
    if candidates != recomputed_candidates:
        raise ValueError("Stage 14 Render candidate evidence is not an exact recomputation")

    matches = [
        candidate
        for candidate in recomputed_candidates
        if candidate["code_offset"] == candidate_offset
    ]
    if len(matches) != 1:
        raise ValueError("candidate is not a unique recorded Render offset")
    candidate = matches[0]

    actual_candidate = data[candidate_offset:candidate_end]
    if actual_candidate != bytes(RELAY_LENGTH):
        raise ValueError("candidate is not 32 zero bytes")
    candidate_hex = actual_candidate.hex().upper()

    fallback_offset = _require_int(
        candidate.get("fallback_offset"), "fallback offset"
    )
    slot_offset = _require_int(candidate.get("slot_offset"), "slot offset")
    if fallback_offset < candidate_offset or fallback_offset + FALLBACK_LENGTH > candidate_end:
        raise ValueError("fallback extends beyond candidate window")
    if slot_offset < candidate_offset or slot_offset + SLOT_LENGTH > candidate_end:
        raise ValueError("slot extends beyond candidate window")
    if fallback_offset != candidate_offset + FALLBACK_DELTA:
        raise ValueError("fallback offset does not match the recorded relay layout")
    if slot_offset != candidate_offset + SLOT_DELTA:
        raise ValueError("slot offset does not match the recorded relay layout")

    branch_reachable = is_branch_reachable(render_offset, candidate_offset)
    if not branch_reachable:
        raise ValueError("candidate is outside the AArch64 entry branch range")
    branch_hex = encode_branch(render_offset, candidate_offset).hex().upper()
    if candidate.get("entry_branch_hex") != branch_hex:
        raise ValueError("candidate branch encoding does not match Stage 14 evidence")

    surrounding_start = max(text_start, candidate_offset - SURROUNDING_BEFORE)
    surrounding_end = min(text_end, candidate_end + SURROUNDING_AFTER)
    return {
        "build_id": build_id,
        "source_sha256": source_sha256,
        "render_offset": render_offset,
        "candidate_offset": candidate_offset,
        "text_range": {"start": text_start, "end": text_end},
        "candidate_range": {"start": candidate_offset, "end": candidate_end},
        "candidate_bytes": candidate_hex,
        "entry_guard": entry_guard,
        "surrounding_text_range": {
            "start": surrounding_start,
            "end": surrounding_end,
        },
        "surrounding_text_bytes": data[
            surrounding_start:surrounding_end
        ].hex().upper(),
        "reserved_intervals": [
            {"start": reserved.start, "end": reserved.stop}
            for reserved in RESERVED_INTERVALS
        ],
        "reserved_overlap": False,
        "branch": {
            "source": render_offset,
            "destination": candidate_offset,
            "delta": candidate_offset - render_offset,
            "minimum_delta": -0x08000000,
            "maximum_delta": 0x07FFFFFC,
            "reachable": branch_reachable,
            "encoding_hex": branch_hex,
        },
        "fallback": {
            "offset": fallback_offset,
            "length": FALLBACK_LENGTH,
            "guard_bytes": data[
                fallback_offset:fallback_offset + FALLBACK_LENGTH
            ].hex().upper(),
            "inside_candidate_window": True,
        },
        "slot": {
            "offset": slot_offset,
            "length": SLOT_LENGTH,
            "guard_bytes": data[
                slot_offset:slot_offset + SLOT_LENGTH
            ].hex().upper(),
            "inside_candidate_window": True,
        },
        "status": "needs_manual_control_flow_review",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("--candidate", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    review = inspect_render_candidate(args.nro, args.candidate, args.evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(review, ensure_ascii=True, indent=2) + "\n", encoding="utf-8"
    )
    print(f"status={review['status']} output={args.output}")


if __name__ == "__main__":
    main()
