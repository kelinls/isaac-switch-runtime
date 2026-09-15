"""Generate version-locked, read-only SaveDataManager observation relays."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_patches import encode_adr, encode_bl, encode_branch
from tools.nro_ips import encode_ips
from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256

SAVE_CALL = 0x36F474
LOAD_CALL = 0x379294
SAVE_PLT = 0x671B00
LOAD_PLT = 0x671AC0
SAVE_RELAY = 0x68CEA0
LOAD_RELAY = 0x68CEF0
CALLBACK_SLOT = 0x68CF90
RELAY_LENGTH = 0x50
CAVE_LENGTH = 0x100


def build_relay(code: int, original_plt: int, resume: int, kind: int) -> bytes:
    if kind not in (1, 2):
        raise ValueError("invalid SaveDataManager observation kind")
    body = bytearray.fromhex(
        "FF8300D1"  # sub sp, sp, #0x20
        "E00700A9"  # stp x0, x1, [sp]
        "FE0B00F9"  # str x30, [sp, #0x10]
    )
    body += (0x52800001 | (kind << 5)).to_bytes(4, "little")  # mov w1, #kind
    body += encode_adr(code + len(body), CALLBACK_SLOT, 17)
    body += bytes.fromhex(
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore
        "00023FD6"  # blr x16
        "E00740A9"  # ldp x0, x1, [sp]
        "FE0B40F9"  # ldr x30, [sp, #0x10]
        "FF830091"  # add sp, sp, #0x20
    )
    body += encode_bl(code + len(body), original_plt)
    body += encode_branch(code + len(body), resume)
    if len(body) > RELAY_LENGTH:
        raise ValueError("relay exceeds reserved region")
    return bytes(body) + bytes(RELAY_LENGTH - len(body))


SAVE_RELAY_CODE = build_relay(SAVE_RELAY, SAVE_PLT, SAVE_CALL + 4, 1)
LOAD_RELAY_CODE = build_relay(LOAD_RELAY, LOAD_PLT, LOAD_CALL + 4, 2)


def build_ips(nro: bytes) -> bytes:
    if hashlib.sha256(nro).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if nro[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    if nro[SAVE_RELAY:SAVE_RELAY + CAVE_LENGTH] != bytes(CAVE_LENGTH):
        raise ValueError("SaveDataManager relay cave is not zero-filled")
    if nro[SAVE_CALL:SAVE_CALL + 4] != bytes.fromhex("A3090C94"):
        raise ValueError("SaveDataManager save call instruction mismatch")
    if nro[LOAD_CALL:LOAD_CALL + 4] != bytes.fromhex("0BE20B94"):
        raise ValueError("SaveDataManager load call instruction mismatch")
    return encode_ips([
        (SAVE_CALL, encode_branch(SAVE_CALL, SAVE_RELAY)),
        (LOAD_CALL, encode_branch(LOAD_CALL, LOAD_RELAY)),
        (SAVE_RELAY, SAVE_RELAY_CODE),
        (LOAD_RELAY, LOAD_RELAY_CODE),
    ])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage128_save_data_manager_observation_relay.py <Repentance.nro> <output-ips>")
    Path(sys.argv[2]).write_bytes(build_ips(Path(sys.argv[1]).read_bytes()))
    print(f"wrote={sys.argv[2]}")
