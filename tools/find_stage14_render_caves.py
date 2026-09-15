"""Enumerate version-locked Stage 14 Render relay cave candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


RELAY_LENGTH = 0x20
FALLBACK_DELTA = 0x10
SLOT_DELTA = 0x18
RESERVED_INTERVALS = (
    range(0x68CBE0, 0x68CC00),
    range(0x68CC20, 0x68CC40),
)


def is_branch_reachable(source: int, destination: int) -> bool:
    """Return whether one AArch64 B instruction can reach the destination."""
    delta = destination - source
    return delta % 4 == 0 and -0x08000000 <= delta <= 0x07FFFFFC


def encode_branch(source: int, destination: int) -> bytes:
    """Encode one checked AArch64 unconditional B instruction."""
    if not is_branch_reachable(source, destination):
        raise ValueError("ARM64 B target is unaligned or out of range")
    delta = destination - source
    return (0x14000000 | ((delta >> 2) & 0x03FFFFFF)).to_bytes(4, "little")


def find_render_caves(
    data: bytes,
    render_offset: int,
    *,
    reserved: tuple[range, ...],
) -> list[dict[str, int | str]]:
    """Return every aligned relay-sized zero range in the NRO text section."""
    candidates: list[dict[str, int | str]] = []
    if len(data) < 0x28 or data[0x10:0x14] != b"NRO0":
        raise ValueError("NRO0 header not found")
    text_offset = int.from_bytes(data[0x20:0x24], "little")
    text_size = int.from_bytes(data[0x24:0x28], "little")
    text_end = text_offset + text_size
    if (
        text_offset % 4
        or text_size < RELAY_LENGTH
        or text_offset > len(data)
        or text_end > len(data)
    ):
        raise ValueError("NRO text section is outside the file")

    for offset in range(text_offset, text_end - RELAY_LENGTH + 1, 4):
        interval = range(offset, offset + RELAY_LENGTH)
        if any(
            interval.start < item.stop and item.start < interval.stop
            for item in reserved
        ):
            continue
        if data[offset:offset + RELAY_LENGTH] != bytes(RELAY_LENGTH):
            continue
        if not is_branch_reachable(render_offset, offset):
            continue
        candidates.append(
            {
                "code_offset": offset,
                "length": RELAY_LENGTH,
                "entry_branch_hex": encode_branch(render_offset, offset).hex().upper(),
                "fallback_offset": offset + FALLBACK_DELTA,
                "slot_offset": offset + SLOT_DELTA,
                "first_32_bytes": data[offset:offset + RELAY_LENGTH].hex().upper(),
            }
        )
    return candidates


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("--render-offset", type=lambda value: int(value, 0), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    candidates = find_render_caves(
        args.nro.read_bytes(),
        args.render_offset,
        reserved=RESERVED_INTERVALS,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(candidates, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"candidates={len(candidates)} output={args.output}")


if __name__ == "__main__":
    main()
