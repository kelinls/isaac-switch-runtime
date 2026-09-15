"""Generate version-locked, read-only Save/Load post-call observation relays."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.build_patches import encode_adr, encode_bl, encode_branch
from tools.nro_ips import encode_ips
from tools.stage114_managed_save_pipeline import BUILD_ID, SOURCE_SHA256

SAVE_CALL = 0x3FA7B4
LOAD_CALL = 0x3FAB60
SAVE_PLT = 0x67CC90
LOAD_PLT = 0x67CD00
SAVE_RELAY = 0x68CEA0
LOAD_RELAY = 0x68CEF0
CALLBACK_SLOT = 0x68CF90
RELAY_LENGTH = 0x50
CAVE_LENGTH = 0x100


def build_relay(code: int, original_plt: int, resume: int, manager_register: bytes,
                kind: int, preserve_load_result: bool = False) -> bytes:
    if kind not in (1, 2):
        raise ValueError("invalid Save/Load observation kind")
    body = bytearray.fromhex(
        "FF8300D1"  # sub sp, sp, #0x20
        "E00700A9"  # stp x0, x1, [sp]
        "FE0B00F9"  # str x30, [sp, #0x10]
    )
    body += encode_bl(code + len(body), original_plt)
    if preserve_load_result:
        body += bytes.fromhex("E01F00B9")  # str w0, [sp, #0x1c]
    body += manager_register
    body += bytes.fromhex("20008052")  # mov w1, #1 (kind overwritten below)
    body[-4:] = (0x52800001 | (kind << 5)).to_bytes(4, "little")
    body += encode_adr(code + len(body), CALLBACK_SLOT, 17)
    body += bytes.fromhex(
        "30FEDFC8"  # ldar x16, [x17]
        "500000B4"  # cbz x16, restore
        "00023FD6"  # blr x16
        "E00740A9"  # ldp x0, x1, [sp]
        "FE0B40F9"  # ldr x30, [sp, #0x10]
    )
    if preserve_load_result:
        body += bytes.fromhex("E01F40B9")  # ldr w0, [sp, #0x1c]
    body += bytes.fromhex("FF830091")  # add sp, sp, #0x20
    body += encode_branch(code + len(body), resume)
    if len(body) > RELAY_LENGTH:
        raise ValueError("relay exceeds reserved region")
    return bytes(body) + bytes(RELAY_LENGTH - len(body))


SAVE_RELAY_CODE = build_relay(SAVE_RELAY, SAVE_PLT, SAVE_CALL + 4, bytes.fromhex("E00313AA"), 1)
LOAD_RELAY_CODE = build_relay(LOAD_RELAY, LOAD_PLT, LOAD_CALL + 4, bytes.fromhex("E00314AA"), 2, True)


def build_ips(nro: bytes) -> bytes:
    if hashlib.sha256(nro).hexdigest() != SOURCE_SHA256:
        raise ValueError("unsupported NRO source_sha256")
    if nro[0x40:0x54].hex().upper() != BUILD_ID:
        raise ValueError("unsupported NRO build ID")
    if nro[SAVE_RELAY:SAVE_RELAY + CAVE_LENGTH] != bytes(CAVE_LENGTH):
        raise ValueError("Save/Load relay cave is not zero-filled")
    return encode_ips([
        (SAVE_CALL, encode_branch(SAVE_CALL, SAVE_RELAY)),
        (LOAD_CALL, encode_branch(LOAD_CALL, LOAD_RELAY)),
        (SAVE_RELAY, SAVE_RELAY_CODE),
        (LOAD_RELAY, LOAD_RELAY_CODE),
    ])


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: stage127_save_load_observation_relay.py <Repentance.nro> <output-ips>")
    Path(sys.argv[2]).write_bytes(build_ips(Path(sys.argv[1]).read_bytes()))
    print(f"wrote={sys.argv[2]}")
