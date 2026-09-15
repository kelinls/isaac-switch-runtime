"""Export static evidence for game-owned content-reader candidates."""

import argparse
import json
from pathlib import Path

from tools.nro_symbols import parse_dynamic_relocations, parse_dynamic_symbols


TARGET_BUILD_ID = "91C73FDD575061318D68886316AFEAC72388B2AB"
CANDIDATES = {
    "getMountedFilePath": "_ZN4KAGE7Filesys15IContentManager18GetMountedFilePathEPKc",
    "getFileMountPoint": "_ZN4KAGE7Filesys15IContentManager17GetFileMountPointEPKc",
    "getDirectoryEntries": "_ZN4KAGE7Filesys15IContentManager19GetDirectoryEntriesEPKcRj",
    "nnFsOpenFile": "_ZN2nn2fs8OpenFileEPNS0_10FileHandleEPKci",
    "nnFsGetFileSize": "_ZN2nn2fs11GetFileSizeEPlNS0_10FileHandleE",
    "nnFsReadFile": "_ZN2nn2fs8ReadFileEPmNS0_10FileHandleElPvm",
}
NEXT_REQUIRED_EVIDENCE = [
    "ABI",
    "object/global ownership",
    "main-thread call site",
    "file size strategy",
    "RomFS overlay read diagnostic",
]


def export_content_reader_evidence(nro_path: Path, output_path: Path) -> dict[str, object]:
    data = nro_path.read_bytes()
    build_id, symbols = parse_dynamic_symbols(data)
    relocation_build_id, relocations = parse_dynamic_relocations(data)
    if build_id != TARGET_BUILD_ID:
        raise ValueError(f"unsupported NRO build ID: {build_id}")
    if relocation_build_id != build_id:
        raise ValueError("dynamic symbol and relocation build IDs disagree")

    candidates: dict[str, dict[str, object]] = {}
    for key, symbol_name in CANDIDATES.items():
        symbol = symbols.get(symbol_name)
        if symbol is None:
            raise ValueError(f"missing content-reader candidate: {symbol_name}")
        if not symbol.is_defined:
            candidates[key] = {
                "symbol": symbol.name,
                "symbol_kind": "undefined_import",
                "file_offset": None,
                "first_16_bytes": None,
                "relocations": [
                    {
                        "table": entry.table,
                        "target_offset": entry.offset,
                        "relocation_type": entry.relocation_type,
                        "addend": entry.addend,
                    }
                    for entry in relocations.get(symbol.name, [])
                ],
            }
            continue
        if symbol.file_offset is None or symbol.file_offset + 16 > len(data):
            raise ValueError(f"candidate guard is outside the NRO: {symbol_name}")
        candidates[key] = {
            "symbol": symbol.name,
            "symbol_kind": "defined_nro",
            "file_offset": symbol.file_offset,
            "first_16_bytes": data[symbol.file_offset:symbol.file_offset + 16].hex().upper(),
        }

    evidence: dict[str, object] = {
        "build_id": build_id,
        "program": nro_path.name,
        "candidates": candidates,
        "direct_runtime_call_permitted": False,
        "next_required_evidence": NEXT_REQUIRED_EVIDENCE,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export_content_reader_evidence(args.nro, args.output)


if __name__ == "__main__":
    main()
