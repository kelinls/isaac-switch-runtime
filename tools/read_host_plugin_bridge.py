#!/usr/bin/env python3
"""Decode the host plugin's `isaac-runtime-bridge.bin` journal.

The SaltyNX host plugin appends fixed-size records to
`sdmc:/SaltySD/plugins/010021C000B6A000/isaac-runtime-bridge.bin`. Every record
starts with an 8-byte magic; everything after it is little-endian. Keeping one
reader in the repo means on-device evidence is decoded from the bytes instead of
from a recollection of what the plugin was supposed to write.

The same reader handles `isaac-runtime-self.bin`, which the Runtime writes by
itself through the `fs` service with no plugin in the path: its records start with
`ISAACRS1`.

Usage:
    python3 tools/read_host_plugin_bridge.py <bridge.bin> [<bridge.bin> ...]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

FLAG_NAMES = {
    0: "device-verified",
    1: "runtime-present",
    2: "api-registered",
    3: "snapshot-magic",
    4: "create-probe-ok",
    5: "snapshot-exported",
    6: "observer-state-read",
    7: "hook-diagnostics-resolved",
    8: "hook-diagnostics-read",
    9: "diagnostics-state-resolved",
    10: "diagnostics-state-read",
    11: "runtime-identity-resolved",
    12: "file-table-registered-in-running-copy",
    13: "runtime-copy-confirmed",
    14: "self-journal-state-resolved",
    15: "self-journal-state-read",
    16: "module-copy-scan-done",
    17: "running-copy-found",
    18: "file-table-planted-in-running-copy",
    19: "running-copy-state-read",
    20: "running-copy-journal-read",
    21: "handover-thread-started",
    22: "plugin-thread-file-write-ok",
}

# magic -> (record size, human name)
RECORDS = {
    b"ISAACBR1": (16, "bridge v1 (legacy)"),
    b"ISAACBR2": (32, "bridge v2"),
    b"ISAACSN1": (80, "runtime snapshot"),
    b"ISAACNF1": (32, "create probe"),
    b"ISAACHD1": (64, "hook diagnostics"),
    b"ISAACDS1": (48, "diagnostics state"),
    b"ISAACJS1": (88, "self-journal state (read by the plugin)"),
    b"ISAACRS1": (144, "self-journal record (written by the Runtime)"),
    b"ISAACMM1": (176, "mapping probe v4"),
    b"ISAACSG1": (224, "module copy scan (bytes, not sizes)"),
    b"ISAACWT1": (96, "wait for the Runtime entry + hand-over"),
    b"ISAACIO1": (96, "plugin-thread file write probe"),
    b"ISAACHS1": (48, "handover thread start (create/start result codes)"),
}

# Read indices are wire format: 1 = the plugin's first sample, 2 = its second sample,
# 3 = read from the copy that actually runs `exl_main`.
READ_INDEX_NAMES = {1: "first sample", 2: "second sample", 3: "running copy"}

HOOK_WORDS = (
    "updateInstall",
    "renderInstall",
    "preGetCollectibleRelay",
    "updateCallbackEntries",
    "renderCallbackEntries",
)

DIAGNOSTICS_WORDS = (
    "fileApiMask",
    "sessionCreated",
    "registrationCalls",
    "gateStoppedEarly",
    "handshake",
)

SELF_JOURNAL_WORDS = (
    "updateAttempts",
    "updateRecords",
    "renderAttempts",
    "renderRecords",
    "exlMainAttempts",
    "exlMainRecords",
    "smInitialize",
    "fsInitialize",
    "fsOpenSdCardFileSystem",
    "fsFsOpenFile",
    "fsFsCreateFile",
    "fsFileWrite",
    "totalRecords",
    "lastMarker",
    "serviceState",
    "threadPointer",
)

SELF_JOURNAL_MARKERS = {1: "update callback", 2: "render callback", 3: "exl_main"}
SERVICE_STATES = {0: "not attempted", 1: "ready", 2: "failed"}
THREAD_POINTER_STATES = {0: "already set", 1: "installed by the Runtime", 2: "no thread region"}

SNAPSHOT_WORDS = (
    ("magic(low)", 0),
    ("version", 8),
    ("state", 12),
    ("detail", 16),
    ("sequence", 20),
    ("buildId(low)", 24),
    ("kind", 32),
    ("stage", 36),
    ("reserved(diagAttach)", 40),
    ("checksum", 44),
)

ATTACH_STATES = {
    0: "NotAttempted",
    1: "Attached",
    2: "NoPort",
    3: "OpenFailed",
    4: "SessionMissing",
}


def u32(buf: bytes, offset: int) -> int:
    return struct.unpack_from("<I", buf, offset)[0]


def u64(buf: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", buf, offset)[0]


def fnv1a(data: bytes) -> int:
    value = 0x811C9DC5
    for byte in data:
        value ^= byte
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


RESULT_FIELDS = (
    "smInitialize",
    "fsInitialize",
    "fsOpenSdCardFileSystem",
    "fsFsOpenFile",
    "fsFsCreateFile",
    "fsFileWrite",
)


def result_text(value: int) -> str:
    """Render a Nintendo `Result`: bit 0 is failure, bits 9..20 the module."""
    module = (value >> 9) & 0xFFF
    description = value & 0x1FF
    return f"0x{value:08x} (module {module}, description {description})"


def format_flags(flags: int) -> str:
    parts = [name for bit, name in FLAG_NAMES.items() if flags & (1 << bit)]
    unknown = flags & ~((1 << 14) - 1)
    if unknown:
        parts.append(f"unknown(0x{unknown:x})")
    return f"0x{flags:08x} [" + ", ".join(parts) + "]"


def decode_record(magic: bytes, record: bytes, index: int, offset: int) -> list[str]:
    lines = [f"[{index:03d}] +{offset:06x} {magic.decode()} ({RECORDS[magic][1]})"]
    if magic == b"ISAACBR1":
        lines.append(f"        legacyWord = {u64(record, 8):#x}")
    elif magic == b"ISAACBR2":
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
    elif magic == b"ISAACNF1":
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
    elif magic == b"ISAACHS1":
        live = u32(record, 20)
        attempted = u32(record, 24)
        create_result = u32(record, 28)
        start_result = u32(record, 32)
        started_flag = u32(record, 36)
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
        lines.append(f"        helperLive={live} attempted={attempted} "
                     f"startedFlag={started_flag}")
        lines.append(f"        svcCreateThread -> "
                     f"{result_text(create_result) if attempted else 'not attempted'}")
        if not attempted or create_result != 0:
            lines.append("        svcStartThread  -> n/a（创建未成功，未走到启动）")
        else:
            lines.append(f"        svcStartThread  -> {result_text(start_result)}")
        lines.append(f"        checksum ok = {u32(record, 44) == fnv1a(record[:44])}")
    elif magic == b"ISAACSN1":
        version, read_index = record[8], record[9]
        lines.append(f"        version={version} readIndex={read_index} "
                     f"({READ_INDEX_NAMES.get(read_index, '?')}) "
                     f"flags={format_flags(u32(record, 12))} len={u32(record, 16)}")
        lines.append(f"        snapshotResult = {u64(record, 20):#x}")
        raw = record[24:72]
        fields = []
        for name, position in SNAPSHOT_WORDS:
            fields.append(f"{name}={u32(raw, position)}")
        lines.append("        snapshot: " + " ".join(fields))
        lines.append(f"        snapshot.magic(u64) = {u64(raw, 0):#x} "
                     f"(expected 0x3152544341415349 'ISAACTR1')")
        lines.append(f"        snapshot.buildId(u64) = {u64(raw, 24)}")
        reserved = u32(raw, 40)
        # 低 8 位是诊断 attach；高 24 位是挂点安装报告（Task 5），必须掩码后按语义解码，
        # 否则 ATTACH_STATES 永远查不到（整字带高位）。
        attach = reserved & 0xFF
        lines.append(f"        snapshot.reserved.lo8(diagAttach) = {attach} "
                     f"({ATTACH_STATES.get(attach, '?')})")
        # 位 8-15 已安装数 / 位 16-23 失败挂点序号(index+1, 0=无) / 位 24-31 入口中继失败码(0=无)。
        installed = (reserved >> 8) & 0xFF
        failure_slot = (reserved >> 16) & 0xFF
        failure_code = (reserved >> 24) & 0xFF
        lines.append(f"        hook installed={installed} failureSlot={failure_slot} "
                     f"failureCode={failure_code}")
        lines.append(f"        checksum ok = {u32(record, 72) == fnv1a(record[:72])}")
    elif magic == b"ISAACHD1":
        lines.append(f"        version={record[8]} readIndex={record[9]} "
                     f"flags={format_flags(u32(record, 12))} len={u32(record, 16)}")
        for position, name in enumerate(HOOK_WORDS):
            lines.append(f"        words[{position}] {name} = {u32(record, 20 + position * 4)}")
        lines.append(f"        checksum ok = {u32(record, 60) == fnv1a(record[:60])}")
    elif magic == b"ISAACDS1":
        lines.append(f"        version={record[8]} readIndex={record[9]} "
                     f"({READ_INDEX_NAMES.get(record[9], '?')}) "
                     f"flags={format_flags(u32(record, 12))} len={u32(record, 16)}")
        for position, name in enumerate(DIAGNOSTICS_WORDS):
            lines.append(f"        words[{position}] {name} = {u32(record, 20 + position * 4)}")
        lines.append(f"        checksum ok = {u32(record, 44) == fnv1a(record[:44])}")
    elif magic == b"ISAACJS1":
        lines.append(f"        version={record[8]} readIndex={record[9]} "
                     f"({READ_INDEX_NAMES.get(record[9], '?')}) "
                     f"flags={format_flags(u32(record, 12))} len={u32(record, 16)}")
        for position, name in enumerate(SELF_JOURNAL_WORDS):
            value = u32(record, 20 + position * 4)
            note = ""
            if name in ("serviceState", "threadPointer"):
                table = SERVICE_STATES if name == "serviceState" else THREAD_POINTER_STATES
                note = f" ({table.get(value, '?')})"
            elif name == "lastMarker":
                note = f" ({SELF_JOURNAL_MARKERS.get(value, '?')})"
            elif name in RESULT_FIELDS and value:
                note = f" ({result_text(value)})"
            lines.append(f"        words[{position}] {name} = {value}{note}")
        lines.append(f"        checksum ok = "
                     f"{u32(record, 84) == fnv1a(record[:84])}")
    elif magic == b"ISAACRS1":
        lines.append(f"        version={u32(record, 8)} "
                     f"marker={u32(record, 12)} ({SELF_JOURNAL_MARKERS.get(u32(record, 12), '?')}) "
                     f"attempt={u32(record, 16)} recordsBefore={u32(record, 20)}")
        writer = u64(record, 24)
        published = u64(record, 32)
        lines.append(f"        writerAddress    = {writer:#x}"
                     + (f"  (base {writer - 0xe764:#x} if this is the 2026-09-11 layout)"
                        if writer else "  (no writer address)"))
        lines.append(f"        publishedExlMain = {published:#x}"
                     + (f"  (base {published - 0x5bc0:#x} if this is the 2026-09-11 layout)"
                        if published else "  (exl_main never published here)"))
        lines.append(f"        buildId={u64(record, 40)} fileApiMask={u32(record, 48)} "
                     f"sessionCreated={u32(record, 52)} registrationCalls={u32(record, 56)} "
                     f"gateStoppedEarly={u32(record, 60)} handshake={u32(record, 64):#x}")
        for position, name in enumerate(HOOK_WORDS):
            lines.append(f"        hook[{position}] {name} = {u32(record, 68 + position * 4)}")
        for position, name in enumerate(SELF_JOURNAL_WORDS[6:12]):
            value = u32(record, 88 + position * 4)
            lines.append(f"        {name} = {value}" + (f" ({result_text(value)})" if value else ""))
        total = u32(record, 120)
        tls = u32(record, 124)
        lines.append(f"        totalRecordsBefore={total} threadPointer={tls} "
                     f"({THREAD_POINTER_STATES.get(tls, '?')}) tick={u64(record, 128)}")
        lines.append(f"        checksum ok = {u32(record, 140) == fnv1a(record[:140])}")
    elif magic == b"ISAACIO1":
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
        lines.append(f"        attempts={u32(record, 20)} openOk={u32(record, 24)} "
                     f"written={u32(record, 28)} closeOk={u32(record, 32)}")
        lines.append(f"        openFn={u64(record, 40):#x} writeFn={u64(record, 48):#x} "
                     f"closeFn={u64(record, 56):#x} tick={u64(record, 64)}")
        lines.append(f"        checksum ok = {u32(record, 92) == fnv1a(record[:92])}")
    elif magic == b"ISAACWT1":
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
        lines.append(f"        polls={u32(record, 20)} registrations={u32(record, 24)}")
        lines.append(f"        final: mask={u32(record, 28)} sessionCreated={u32(record, 32)} "
                     f"registrationCalls={u32(record, 36)} gateStoppedEarly={u32(record, 40)} "
                     f"handshake={u32(record, 44):#x}")
        entry = u64(record, 48)
        lines.append(f"        publishedExlMain = {entry:#x}"
                     + ("   <== the Runtime's entry had run when the table was handed over"
                        if entry else "   (the entry never ran inside the wait window)"))
        lines.append(f"        tickSelfAppeared={u64(record, 56)} tickRegistered={u64(record, 64)} "
                     f"tickNow={u64(record, 72)}")
        state = SERVICE_STATES.get(u32(record, 88), "?")
        lines.append(f"        selfJournal: exlMainAttempts={u32(record, 80)} "
                     f"exlMainRecords={u32(record, 84)} serviceState={u32(record, 88)} ({state})")
        lines.append(f"        checksum ok = {u32(record, 92) == fnv1a(record[:92])}")
    elif magic == b"ISAACSG1":
        lines.append(f"        version={record[8]} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
        lines.append(f"        regionsVisited={u32(record, 20)} "
                     f"regionsScanned={u32(record, 24)} copies={u32(record, 28)}")
        own_base = u64(record, 32)
        own_identity = u64(record, 40)
        running_identity = u64(record, 48)
        running_self = u64(record, 56)
        planted = u64(record, 64)
        slot_delta = struct.unpack_from("<q", record, 72)[0]
        lines.append(f"        lookupCopyRegion = {own_base:#x}   lookupIdentity = {own_identity:#x}")
        lines.append(f"        runningIdentity  = {running_identity:#x}"
                     + (f"   (running base {running_identity - own_identity + own_base:#x})"
                        if running_identity and own_identity else "   (no running copy found)"))
        lines.append(f"        runningSelf      = {running_self:#x}"
                     + ("   <== the copy that ran exl_main" if running_self else ""))
        lines.append(f"        plantedSlot      = {planted:#x}   slotDelta = {slot_delta:#x}")
        for position in range(6):
            if position >= u32(record, 28):
                break
            hit = record[80 + position * 24:80 + position * 24 + 24]
            base = u64(hit, 0)
            self_address = u64(hit, 8)
            size = u32(hit, 16)
            type_perm = u32(hit, 20)
            lines.append(f"        copy[{position}] base={base:#x} size={size:#x} "
                         f"type={type_perm & 0xFF:#x} perm={type_perm >> 8:#x} "
                         f"self={self_address:#x}")
        lines.append(f"        checksum ok = {u32(record, 220) == fnv1a(record[:220])}")
        if u32(record, 28) >= 2:
            lines.append("        NOTE: more than one copy of the module is mapped")
    elif magic == b"ISAACMM1":
        version = record[8]
        lines.append(f"        version={version} flags={format_flags(u32(record, 12))} "
                     f"len={u32(record, 16)}")
        lines.append(f"        visited={u32(record, 20)} ownFound={u32(record, 24)} "
                     f"ownType={u32(record, 28):#x} ownPerm={u32(record, 32):#x}")
        own_base = u32(record, 36) | (u32(record, 40) << 32)
        own_size = u32(record, 44) | (u32(record, 48) << 32)
        own_identity = u32(record, 52) | (u32(record, 56) << 32)
        running_self = u32(record, 60) | (u32(record, 64) << 32)
        same_size_count = u32(record, 68)
        running_index = u32(record, 72)
        lines.append(f"        ownRegion  = {own_base:#x}..{own_base + own_size:#x} "
                     f"(size {own_size:#x})")
        lines.append(f"        ownIdentity= {own_identity:#x} "
                     f"(base+{own_identity - own_base:#x})" if own_base else
                     f"        ownIdentity= {own_identity:#x}")
        lines.append(f"        runningSelf= {running_self:#x} "
                     + (f"(exl_main)" if running_self else "(never published)"))
        lines.append(f"        sameSizeCount={same_size_count} runningIndexPlus1={running_index}")
        for position in range(6):
            base = u32(record, 76 + position * 12) | (u32(record, 80 + position * 12) << 32)
            size = u32(record, 84 + position * 12)
            if base == 0 and size == 0:
                continue
            mark = ""
            if running_index and position == running_index - 1:
                mark = "  <== running copy"
            lines.append(f"        region[{position}] {base:#x}..{base + size:#x} "
                         f"(size {size:#x}){mark}")
        lines.append(f"        checksum ok = {u32(record, 172) == fnv1a(record[:172])}")
    return lines


def decode(path: Path) -> None:
    data = path.read_bytes()
    print(f"=== {path} ({len(data)} bytes)")
    offset = 0
    index = 0
    while offset + 8 <= len(data):
        magic = data[offset:offset + 8]
        if magic not in RECORDS:
            print(f"[{index:03d}] +{offset:06x} UNKNOWN magic {magic!r} -- resynchronising")
            offset += 1
            continue
        size = RECORDS[magic][0]
        record = data[offset:offset + size]
        if len(record) < size:
            print(f"[{index:03d}] +{offset:06x} TRUNCATED {magic.decode()} "
                  f"({len(record)}/{size} bytes)")
            break
        for line in decode_record(magic, record, index, offset):
            print(line)
        offset += size
        index += 1
    if offset != len(data):
        print(f"    trailing {len(data) - offset} bytes after the last complete record")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    for argument in argv[1:]:
        decode(Path(argument))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
