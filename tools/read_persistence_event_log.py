#!/usr/bin/env python3
import argparse
import json
import struct
import sys
from pathlib import Path


EVENT_MAGIC = b"ISAACPE1"
REGISTRATION_MAGIC = b"ISAACRG1"
EVENT_SIZE = 64
REGISTRATION_SIZE = 48
EVENT_NAMES = {
    1: "FileApiAccepted", 2: "ManagerCallbackEntered", 3: "OriginalReturned",
    4: "ManifestEntered", 5: "ManifestReturned", 6: "GateEvaluated",
    7: "DispatchEntered", 8: "DispatchReturned", 9: "OperationEntered",
    10: "OperationReturned", 11: "CallbackFailed", 12: "FlushConfirmation",
    13: "QueueOverflow",
}
OPERATION_NAMES = {0: "None", 1: "SaveData", 2: "LoadData", 3: "HasData", 4: "RemoveData"}
FLUSH_NAMES = {0: "NeverAttempted", 1: "Busy", 2: "NoApi", 3: "OpenFailed",
               4: "ShortWrite", 5: "CloseFailed", 6: "Succeeded"}
REGISTRATION_NAMES = {1: "SymbolsResolved", 2: "RegisterReturned"}


def _checksum(data):
    value = 2166136261
    for byte in data:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def _records(data, build_id, size, magic):
    warnings = []
    if len(data) % size:
        warnings.append(f"末尾不足一个完整记录，忽略 {len(data) % size} 字节")
    parsed = []
    last_end = 0
    for offset in range(0, len(data) - size + 1):
        record = data[offset:offset + size]
        if record[:8] != magic:
            continue
        if offset > last_end:
            warnings.append(f"发现损坏区间 {last_end}..{offset}，已重新同步 magic")
        if offset % size:
            warnings.append(f"发现未对齐记录，magic 位于 offset {offset}，前置区域可能由短写造成")
        version, record_size = struct.unpack_from("<II", record, 8)
        current_build = struct.unpack_from("<Q", record, 16)[0]
        if current_build != build_id:
            continue
        if version != 1 or record_size != size:
            raise ValueError(f"invalid protocol at offset {offset}")
        expected = struct.unpack_from("<I", record, size - 4)[0]
        if _checksum(record[:size - 4]) != expected:
            raise ValueError(f"checksum mismatch at offset {offset}")
        parsed.append(record)
        last_end = offset + size
    if data and not parsed:
        warnings.append("未发现可校验记录，数据可能已损坏或格式不匹配")
    return parsed, warnings


def read_event_log(registration_path: Path, event_path: Path, build_id: int):
    registration_data = registration_path.read_bytes() if registration_path.exists() else b""
    event_data = event_path.read_bytes() if event_path.exists() else b""
    registration, registration_warnings = _records(
        registration_data, build_id, REGISTRATION_SIZE, REGISTRATION_MAGIC)
    events, event_warnings = _records(event_data, build_id, EVENT_SIZE, EVENT_MAGIC)
    parsed_events = []
    previous_sequence = None
    for record in events:
        sequence = struct.unpack_from("<I", record, 24)[0]
        if previous_sequence is not None and sequence <= previous_sequence:
            event_warnings.append("sequence 发生重置或回退，按记录顺序继续判读")
        previous_sequence = sequence
        event = struct.unpack_from("<I", record, 28)[0]
        operation = struct.unpack_from("<I", record, 32)[0]
        parsed_events.append({
            "sequence": sequence,
            "event": EVENT_NAMES.get(event, f"Unknown({event})"),
            "event_value": event,
            "operation": OPERATION_NAMES.get(operation, f"Unknown({operation})"),
            "operation_value": operation,
            "result": struct.unpack_from("<I", record, 36)[0],
            "detail": struct.unpack_from("<Q", record, 40)[0],
            "callback_count": struct.unpack_from("<I", record, 48)[0],
            "flags": struct.unpack_from("<I", record, 52)[0],
            "flush": FLUSH_NAMES.get(struct.unpack_from("<I", record, 56)[0], "Unknown"),
        })
    pending = {}
    for item in parsed_events:
        if item["event"] == "OperationEntered":
            pending[item["operation"]] = pending.get(item["operation"], 0) + 1
        elif item["event"] == "OperationReturned":
            count = pending.get(item["operation"], 0)
            if count:
                pending[item["operation"]] = count - 1
            else:
                event_warnings.append(f"未匹配的 {item['operation']} OperationReturned")
    missing_returns = sorted(
        name for name, count in pending.items() if count > 0 and name != "None"
    )
    parsed_registration = []
    for record in registration:
        event = struct.unpack_from("<I", record, 28)[0]
        parsed_registration.append({
            "sequence": struct.unpack_from("<I", record, 24)[0],
            "event": REGISTRATION_NAMES.get(event, f"Unknown({event})"),
            "event_value": event,
            "api_flags": struct.unpack_from("<I", record, 32)[0],
            "register_result": struct.unpack_from("<Q", record, 36)[0],
        })
    flushes = sorted({item["flush"] for item in parsed_events})
    last = parsed_events[-1] if parsed_events else None
    return {
        "build_id": build_id,
        "registration": parsed_registration,
        "events": parsed_events,
        "last_event": last,
        "missing_returns": missing_returns,
        "flush_results": flushes,
        "warnings": registration_warnings + event_warnings,
        "interpretation": "未发现指定 Build ID 事件" if not parsed_events else
            f"最后可信事件：{last['event']}，flush：{last['flush']}",
    }


# --- 统一诊断事件流（diagostics v1）----------------------------------------
# 与 C++ 端 runtime/src/diagnostics/diagnostic_event.hpp 的线格式一一对应。
# 记录为固定 60 字节、逐字段小端写入，magic 在文件里读作 "ISAACDV1"。
DIAGNOSTIC_MAGIC = b"ISAACDV1"
DIAGNOSTIC_RECORD_SIZE = 60
DIAGNOSTIC_SCHEMA_VERSION = 1

DIAGNOSTIC_ORIGIN_NAMES = {0: "Unknown", 1: "RuntimeModule", 2: "HostPlugin"}
DIAGNOSTIC_SUBSYSTEM_NAMES = {
    0: "None", 1: "Bootstrap", 2: "Scanner", 3: "Hook", 4: "Manifest",
    5: "Lua", 6: "Api", 7: "Persistence", 8: "Diagnostics",
}
DIAGNOSTIC_SEVERITY_NAMES = {0: "Trace", 1: "Info", 2: "Warning", 3: "Error"}
DIAGNOSTIC_PHASE_NAMES = {0: "None", 1: "Entered", 2: "Returned", 3: "Failed"}
DIAGNOSTIC_HEALTH_REASON_NAMES = {
    0: "None", 1: "RingOverflow", 2: "OpenFailed", 3: "ShortWrite",
    4: "CloseFailed", 5: "ClaimContended",
}
DIAGNOSTIC_RESULT_DOMAIN_NAMES = {
    0: "None", 1: "Bootstrap", 2: "Platform", 3: "Module", 4: "Hook",
    5: "Manifest", 6: "Lua", 7: "Api", 8: "Persistence", 9: "Diagnostics",
}
# 子系统内的稳定事件号。新事件只能追加，不得改号。
DIAGNOSTIC_EVENT_NAMES = {
    (8, 1): "FilePortRegistered",
    (8, 2): "HealthChanged",
    (8, 3): "JournalFlushed",
    (8, 4): "AttachFailed",
}
# `detail` of an AttachFailed event (wire format, mirrors diagnostic_event_ids.hpp).
DIAGNOSTIC_ATTACH_FAILURE_NAMES = {1: "NoPort", 2: "OpenFailed"}
DIAGNOSTIC_THREAD_UNKNOWN = 0xFFFFFFFF


def _diagnostic_records(data, build_id):
    """按 magic 重新同步并逐条校验，返回 (records, warnings)。"""
    warnings = []
    remainder = len(data) % DIAGNOSTIC_RECORD_SIZE
    if remainder:
        warnings.append(f"末尾不足一个完整诊断记录，忽略 {remainder} 字节")
    records = []
    last_end = 0
    for offset in range(0, len(data) - DIAGNOSTIC_RECORD_SIZE + 1):
        record = data[offset:offset + DIAGNOSTIC_RECORD_SIZE]
        if record[:8] != DIAGNOSTIC_MAGIC:
            continue
        if offset > last_end:
            warnings.append(f"发现损坏区间 {last_end}..{offset}，已重新同步 magic")
        if offset % DIAGNOSTIC_RECORD_SIZE:
            warnings.append(f"发现未对齐记录，magic 位于 offset {offset}，前置区域可能由短写造成")
        schema, record_size = struct.unpack_from("<HH", record, 8)
        current_build = struct.unpack_from("<Q", record, 16)[0]
        if current_build != build_id:
            continue
        if schema != DIAGNOSTIC_SCHEMA_VERSION or record_size != DIAGNOSTIC_RECORD_SIZE:
            raise ValueError(f"invalid diagnostic protocol at offset {offset}")
        expected = struct.unpack_from("<I", record, DIAGNOSTIC_RECORD_SIZE - 4)[0]
        # 校验和覆盖整条记录，且校验和字段本身以 0 参与计算（与 C++ 编码器一致）。
        if _checksum(record[:DIAGNOSTIC_RECORD_SIZE - 4] + b"\x00" * 4) != expected:
            raise ValueError(f"diagnostic checksum mismatch at offset {offset}")
        records.append(record)
        last_end = offset + DIAGNOSTIC_RECORD_SIZE
    if data and not records:
        warnings.append("未发现可校验的诊断记录，数据可能已损坏或格式不匹配")
    return records, warnings


def _diagnostic_fields(record):
    origin = struct.unpack_from("<I", record, 12)[0]
    subsystem = struct.unpack_from("<H", record, 28)[0]
    event = struct.unpack_from("<H", record, 30)[0]
    thread = struct.unpack_from("<I", record, 36)[0]
    return {
        "origin": DIAGNOSTIC_ORIGIN_NAMES.get(origin, f"Unknown({origin})"),
        "origin_value": origin,
        "build_id": struct.unpack_from("<Q", record, 16)[0],
        "sequence": struct.unpack_from("<I", record, 24)[0],
        "subsystem": DIAGNOSTIC_SUBSYSTEM_NAMES.get(subsystem, f"Unknown({subsystem})"),
        "subsystem_value": subsystem,
        "event": DIAGNOSTIC_EVENT_NAMES.get((subsystem, event), f"Event({event})"),
        "event_value": event,
        "severity": DIAGNOSTIC_SEVERITY_NAMES.get(record[32], f"Unknown({record[32]})"),
        "phase": DIAGNOSTIC_PHASE_NAMES.get(record[33], f"Unknown({record[33]})"),
        "phase_value": record[33],
        "flags": struct.unpack_from("<H", record, 34)[0],
        "thread": "Unknown" if thread == DIAGNOSTIC_THREAD_UNKNOWN else thread,
        "thread_value": thread,
        "result_domain": DIAGNOSTIC_RESULT_DOMAIN_NAMES.get(
            struct.unpack_from("<I", record, 40)[0], "Unknown"),
        "result_code": struct.unpack_from("<I", record, 44)[0],
        "detail": struct.unpack_from("<Q", record, 48)[0],
        "attach_failure": (DIAGNOSTIC_ATTACH_FAILURE_NAMES.get(
            struct.unpack_from("<Q", record, 48)[0], None)
            if (subsystem == 8 and event == 4) else None),
    }


def read_diagnostic_event_log(event_path: Path, build_id: int):
    """读取统一诊断事件流：配对 entered/returned 并报告健康状态。

    配对身份是 (origin, build_id, subsystem, event, thread)。健康状态来自
    Diagnostics/HealthChanged 事件；没有该事件说明本次会话没有记录到降级，
    但也不能排除"事件本身丢了"，所以同时报告 attempts 与实际记录数的差额依据。
    """
    data = event_path.read_bytes() if event_path.exists() else b""
    records, warnings = _diagnostic_records(data, build_id)
    events = [_diagnostic_fields(record) for record in records]
    previous_sequence = None
    for item in events:
        if previous_sequence is not None and item["sequence"] <= previous_sequence:
            warnings.append("诊断事件 sequence 发生重置或回退，按记录顺序继续判读")
        previous_sequence = item["sequence"]

    pending = {}
    pairs = []
    for item in events:
        key = (item["origin_value"], item["build_id"], item["subsystem_value"],
               item["event_value"], item["thread_value"])
        if item["phase_value"] == 1:  # Entered
            pending[key] = pending.get(key, 0) + 1
        elif item["phase_value"] in (2, 3):  # Returned / Failed
            count = pending.get(key, 0)
            if count:
                pending[key] = count - 1
                pairs.append({
                    "origin": item["origin"],
                    "subsystem": item["subsystem"],
                    "event": item["event"],
                    "thread": item["thread"],
                    "outcome": item["phase"],
                    "result_code": item["result_code"],
                })
            else:
                warnings.append(
                    f"未匹配的 {item['subsystem']}/{item['event']} {item['phase']}"
                    f"（thread={item['thread']}）")
    missing_returns = [
        {"origin": DIAGNOSTIC_ORIGIN_NAMES.get(event[0], f"Unknown({event[0]})"),
         "origin_value": event[0], "build_id": event[1],
         "subsystem": DIAGNOSTIC_SUBSYSTEM_NAMES.get(event[2], f"Unknown({event[2]})"),
         "subsystem_value": event[2],
         "event": DIAGNOSTIC_EVENT_NAMES.get((event[2], event[3]), f"Event({event[3]})"),
         "event_value": event[3], "thread": event[4], "count": count}
        for event, count in sorted(pending.items()) if count
    ]

    attach_failures = [item for item in events
                       if item["subsystem_value"] == 8 and item["event_value"] == 4]
    health = []
    for item in events:
        if item["subsystem_value"] == 8 and item["event_value"] == 2:
            reason = DIAGNOSTIC_HEALTH_REASON_NAMES.get(item["result_code"],
                                                        f"Unknown({item['result_code']})")
            health.append({"reason": reason, "reason_value": item["result_code"],
                           "sequence": item["sequence"]})
    last = events[-1] if events else None
    return {
        "protocol": "diagnostic-v1",
        "build_id": build_id,
        "records": len(events),
        "events": events,
        "pairs": pairs,
        "missing_returns": missing_returns,
        "health": health,
        "attach_failures": [{"reason": item["attach_failure"], "sequence": item["sequence"]}
                            for item in attach_failures],
        "degraded": bool(health) or bool(attach_failures),
        "last_event": last,
        "warnings": warnings,
        "interpretation": "未发现指定 Build ID 的诊断事件" if not events else
            f"共 {len(events)} 条事件，匹配 {len(pairs)} 对，未返回 {len(missing_returns)} 项" +
            ("；诊断已降级" if health else "；诊断健康"),
    }


# 诊断附件自证记录：运行时在目标文件里留下的 64 字节状态记录（magic "ISAACDG1"）。
# 它使用旧协议的 64 字节形状但 magic 不同，因此旧读取器会把它当作待重新同步的区域跳过。
DIAGNOSTIC_GATE_MAGIC = b"ISAACDG1"
DIAGNOSTIC_GATE_SIZE = 64


def read_diagnostic_gate(event_path: Path):
    """从事件文件里找出附件自证记录，返回附件各阶段的状态序列。

    这是唯一不依赖宿主插件回读的取证通道：运行时在尝试附件时会往目标文件追加
    状态记录，因此"文件不存在"与"文件里记录了失败原因"都能区分。
    """
    data = event_path.read_bytes() if event_path.exists() else b""
    states = []
    offset = 0
    record = 0
    while True:
        found = data.find(DIAGNOSTIC_GATE_MAGIC, offset)
        if found == -1 or found + DIAGNOSTIC_GATE_SIZE > len(data):
            break
        raw = data[found:found + DIAGNOSTIC_GATE_SIZE]
        states.append({
            "record": record,
            "version": raw[8],
            "state": struct.unpack_from("<I", raw, 12)[0],
            "size": struct.unpack_from("<I", raw, 16)[0],
        })
        offset = found + DIAGNOSTIC_GATE_SIZE
        record += 1
    names = {0: "EnteredDiagnosticsPath", 1: "Attached", 2: "NoPort",
             3: "OpenFailed", 4: "SessionMissing"}
    return {
        "path_exists": event_path.exists(),
        "file_size": len(data),
        "records": states,
        "stages": [names.get(item["state"], f"Unknown({item['state']})") for item in states],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-id", type=int, required=True)
    parser.add_argument("--diagnostic-gate", type=Path,
                        help="只解析附件自证记录（magic ISAACDG1）")
    parser.add_argument("--diagnostic", type=Path,
                        help="统一诊断事件流（isaac-runtime-events.bin）；给出时按新协议解析")
    parser.add_argument("registration", type=Path, nargs="?")
    parser.add_argument("events", type=Path, nargs="?")
    args = parser.parse_args()
    if not 0 < args.build_id < 1 << 64:
        parser.error("--build-id must be a non-zero unsigned 64-bit integer")
    try:
        if args.diagnostic_gate is not None:
            result = read_diagnostic_gate(args.diagnostic_gate)
        elif args.diagnostic is not None:
            result = read_diagnostic_event_log(args.diagnostic, args.build_id)
        else:
            if args.registration is None or args.events is None:
                parser.error("需要 registration 与 events 两个位置参数，或使用 --diagnostic")
            result = read_event_log(args.registration, args.events, args.build_id)
    except (OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    for warning in result.get("warnings", []):
        print(f"警告：{warning}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
