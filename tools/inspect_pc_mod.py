"""Write a transparent JSON index for an existing PC Isaac Mod directory."""

import argparse
import json
from pathlib import Path

from tools.pc_mod_manifest import inspect_mod, write_romfs_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mod", nargs="?", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--mods-root", type=Path)
    parser.add_argument("--romfs-output", type=Path)
    args = parser.parse_args()

    if args.mods_root is not None or args.romfs_output is not None:
        if args.mod is not None or args.output is not None:
            parser.error("--mods-root/--romfs-output mode cannot be mixed with MOD or --output")
        if args.mods_root is None or args.romfs_output is None:
            parser.error("--mods-root and --romfs-output must be used together")
        try:
            write_romfs_manifest(args.mods_root, args.romfs_output)
        except (OSError, ValueError) as error:
            parser.exit(1, f"{parser.prog}: error: {error}\n")
        return

    if args.mod is None or args.output is None:
        parser.error("single Mod mode requires MOD and --output")
    try:
        manifest = inspect_mod(args.mod)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as error:
        parser.exit(1, f"{parser.prog}: error: {error}\n")


if __name__ == "__main__":
    main()
