#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--build-id", type=int, required=True)
    parser.add_argument("--stage", type=int, required=True)
    parser.add_argument("--mode", default="startup-probe")
    args = parser.parse_args()
    if not 0 < args.build_id < (1 << 64):
        parser.error("--build-id must be a non-zero unsigned 64-bit integer")
    files = []
    for path in sorted(p for p in args.root.rglob("*") if p.is_file() and p.name != "test-build-manifest.json"):
        files.append({
            "path": path.relative_to(args.root).as_posix(),
            "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    runtime_candidates = [
        path for path in files if path["path"].endswith("/subsdk9") or path["path"] == "subsdk9"
    ]
    module_id = ""
    if runtime_candidates:
        runtime_path = args.root / runtime_candidates[0]["path"]
        data = runtime_path.read_bytes()
        if len(data) >= 0x60:
            module_id = data[0x40:0x60].hex().upper()
    manifest = {
        "protocol": 2,
        "mode": args.mode,
        "build_id": args.build_id,
        "stage": args.stage,
        "runtime_module_id": module_id,
        "files": files,
    }
    (args.root / "test-build-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
