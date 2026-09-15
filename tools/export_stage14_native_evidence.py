"""Export version-locked native API evidence for Stage 14."""

import argparse
import hashlib
import json
from pathlib import Path

from tools.nro_symbols import Symbol, parse_dynamic_symbols


TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"

TARGETS = {
    "game_is_paused": "_ZNK15IsaacRepentance4Game8IsPausedEv",
    "music_current_id": "_ZNK15IsaacRepentance5Music17GetCurrentMusicIDEv",
    "music_pause": "_ZN15IsaacRepentance5Music5PauseEv",
    "music_resume": "_ZN15IsaacRepentance5Music6ResumeEv",
    "manager_render": "_ZN15IsaacRepentance7Manager6RenderEv",
}

OBJECT_ACCESS_FUNCTIONS = {
    "game": {"game_is_paused"},
    "music": {"music_current_id", "music_pause", "music_resume"},
}


def function_evidence(data: bytes, symbol: Symbol) -> dict[str, object]:
    """Return a fixed-size entry guard for one defined, in-file function."""
    if not symbol.is_defined or symbol.file_offset is None:
        raise ValueError(f"required function is not defined: {symbol.name}")
    if symbol.file_offset + 16 > len(data):
        raise ValueError(f"required function guard is outside NRO: {symbol.name}")
    return {
        "symbol": symbol.name,
        "file_offset": symbol.file_offset,
        "first_16_bytes": data[symbol.file_offset:symbol.file_offset + 16].hex().upper(),
        "is_defined": True,
    }


def load_ghidra_evidence(
    ghidra_path: Path,
    build_id: str,
    functions: dict[str, dict[str, object]],
) -> dict[str, object]:
    """Load deterministic observations that exactly match the NRO evidence."""
    try:
        ghidra = json.loads(ghidra_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read Ghidra evidence: {error}") from error
    if not isinstance(ghidra, dict):
        raise ValueError("Ghidra evidence root must be an object")
    if ghidra.get("build_id") != build_id:
        raise ValueError("Ghidra build_id does not match NRO evidence")

    ghidra_functions = ghidra.get("functions")
    if not isinstance(ghidra_functions, dict):
        raise ValueError("Ghidra functions must be an object")
    normalized_functions: dict[str, dict[str, object]] = {}
    for key, nro_function in functions.items():
        ghidra_function = ghidra_functions.get(key)
        if not isinstance(ghidra_function, dict):
            raise ValueError(f"Ghidra function is missing: {key}")
        for field in ("symbol", "file_offset", "first_16_bytes"):
            if ghidra_function.get(field) != nro_function[field]:
                raise ValueError(f"{key}: Ghidra {field} does not match NRO evidence")
        callers = ghidra_function.get("callers")
        instructions = ghidra_function.get("instructions")
        if not isinstance(callers, list) or not all(isinstance(item, str) for item in callers):
            raise ValueError(f"{key}: Ghidra callers must be a string array")
        if not isinstance(instructions, list) or not all(
            isinstance(item, str) for item in instructions
        ):
            raise ValueError(f"{key}: Ghidra instructions must be a string array")
        normalized_functions[key] = {
            "symbol": ghidra_function["symbol"],
            "file_offset": ghidra_function["file_offset"],
            "first_16_bytes": ghidra_function["first_16_bytes"],
            "callers": sorted(set(callers)),
            "instructions": sorted(set(instructions)),
        }

    candidates = ghidra.get("object_candidates")
    if not isinstance(candidates, list):
        raise ValueError("Ghidra object_candidates must be an array")
    candidate_fields = (
        "address",
        "access_function",
        "reference_address",
        "reference_kind",
        "name",
    )
    normalized_candidates = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, dict) or not all(
            isinstance(candidate.get(field), str) for field in candidate_fields
        ):
            raise ValueError(f"Ghidra object_candidates[{index}] has an invalid schema")
        normalized_candidates.append({field: candidate[field] for field in candidate_fields})
    normalized_candidates.sort(key=lambda candidate: tuple(
        candidate[field] for field in candidate_fields
    ))

    return {
        "build_id": build_id,
        "program": ghidra.get("program", ""),
        "functions": normalized_functions,
        "object_candidates": normalized_candidates,
    }


def export_stage14_native_evidence(
    nro_path: Path,
    output_path: Path,
    ghidra_path: Path | None = None,
) -> dict[str, object]:
    """Write deterministic, read-only evidence for the supported NRO build."""
    data = nro_path.read_bytes()
    source_sha256 = hashlib.sha256(data).hexdigest()
    build_id, symbols = parse_dynamic_symbols(data)
    if build_id != TARGET_BUILD_ID:
        raise ValueError(f"unsupported NRO build ID: {build_id}")

    functions: dict[str, dict[str, object]] = {}
    for key, name in TARGETS.items():
        symbol = symbols.get(name)
        if symbol is None:
            raise ValueError(f"missing required function: {name}")
        try:
            functions[key] = function_evidence(data, symbol)
        except ValueError as error:
            raise ValueError(f"{key}: {error}") from error

    evidence: dict[str, object] = {
        "build_id": build_id,
        "source_sha256": source_sha256,
        "functions": functions,
        "ready_for_runtime_binding": False,
        "blocked_reasons": ["object_ownership_unproven", "render_relay_unproven"],
    }
    if ghidra_path is not None:
        ghidra = load_ghidra_evidence(ghidra_path, build_id, functions)
        evidence["ghidra"] = ghidra
        required_object_kinds = {"game", "music"}
        observed_object_kinds = {
            candidate["name"]
            for candidate in ghidra["object_candidates"]
            if candidate["reference_kind"] == "global_object"
            and candidate["name"] in required_object_kinds
            and candidate["access_function"]
            in OBJECT_ACCESS_FUNCTIONS[candidate["name"]]
        }
        if observed_object_kinds == required_object_kinds:
            evidence["blocked_reasons"] = ["render_relay_unproven"]
        evidence["ready_for_runtime_binding"] = False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(evidence, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("nro", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--ghidra", type=Path)
    args = parser.parse_args()
    export_stage14_native_evidence(args.nro, args.output, args.ghidra)
    print(f"wrote={args.output}")


if __name__ == "__main__":
    main()
