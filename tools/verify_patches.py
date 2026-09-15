"""离线验证已生成的 Repentance NRO IPS 补丁。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_patches import (
    GREED_RECORD,
    NORMAL_RECORDS,
    build_greed_patch,
    build_normal_patch,
)
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
PATCHES = (
    (
        "贪婪捐款机补丁",
        ROOT
        / "dist/atmosphere/nro_patches/isaac-repentance-greed-donation-no-jam"
        / "91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
        [GREED_RECORD],
        build_greed_patch,
    ),
    (
        "普通捐款机补丁",
        ROOT
        / "dist/atmosphere/nro_patches/isaac-repentance-normal-donation-no-jam"
        / "91C73FDD575061318D68886316AFEAC72388B2AB000000000000000000000000.ips",
        NORMAL_RECORDS,
        build_normal_patch,
    ),
)


def verify_patch(nro: bytes, path: Path, expected_records: list[tuple[int, bytes]], builder) -> None:
    expected_ips = builder(nro)
    actual_ips = path.read_bytes()
    records = decode_ips(actual_ips)
    if actual_ips != expected_ips or records != expected_records:
        raise ValueError(f"{path} 的 IPS 记录与已验证生成结果不一致")
    patched = apply_records(nro, records)
    for offset, replacement in expected_records:
        if offset < 0x80:
            raise ValueError(f"{path} 试图覆盖 NRO 头部")
        if patched[offset : offset + len(replacement)] != replacement:
            raise ValueError(f"{path} 模拟应用后的字节不正确")


def main() -> None:
    parser = argparse.ArgumentParser(description="验证以撒忏悔版捐款机 IPS 补丁")
    parser.add_argument("--nro", type=Path, required=True)
    args = parser.parse_args()

    nro = args.nro.read_bytes()
    for label, path, record, builder in PATCHES:
        verify_patch(nro, path, record, builder)
        print(f"{label}: 通过")


if __name__ == "__main__":
    main()
