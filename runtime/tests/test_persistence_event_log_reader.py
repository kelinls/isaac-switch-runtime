import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def put_u32(buffer, offset, value):
    buffer[offset:offset + 4] = int(value).to_bytes(4, "little")


def put_u64(buffer, offset, value):
    buffer[offset:offset + 8] = int(value).to_bytes(8, "little")


def checksum(buffer, length):
    value = 2166136261
    for byte in buffer[:length]:
        value = ((value ^ byte) * 16777619) & 0xffffffff
    return value


def event_record(build_id, sequence, event, operation=0, result=0, detail=0,
                 flush=6, flags=0):
    record = bytearray(64)
    record[:8] = b"ISAACPE1"
    put_u32(record, 8, 1)
    put_u32(record, 12, 64)
    put_u64(record, 16, build_id)
    put_u32(record, 24, sequence)
    put_u32(record, 28, event)
    put_u32(record, 32, operation)
    put_u32(record, 36, result)
    put_u64(record, 40, detail)
    put_u32(record, 48, 1)
    put_u32(record, 52, flags)
    put_u32(record, 56, flush)
    put_u32(record, 60, checksum(record, 60))
    return bytes(record)


class PersistenceEventLogReaderTests(unittest.TestCase):
    def test_reader_resynchronizes_after_one_byte_short_write_and_warns_about_alignment(self):
        with tempfile.TemporaryDirectory(prefix="event-reader-short-write-") as temporary:
            root = Path(temporary)
            registration = root / "registration.bin"
            events = root / "events.bin"
            registration.write_bytes(b"")
            events.write_bytes(b"\x00" + event_record(7, 2, 13, flags=2))
            result = subprocess.run(
                ["python3", str(ROOT / "tools/read_persistence_event_log.py"),
                 "--build-id", "7", str(registration), str(events)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(len(payload["events"]), 1)
            self.assertEqual(payload["events"][0]["event"], "QueueOverflow")
            self.assertTrue(any("对齐" in warning for warning in payload["warnings"]))

    def test_reader_filters_build_id_and_reports_pairs_overflow_and_truncated_tail(self):
        with tempfile.TemporaryDirectory(prefix="event-reader-") as temporary:
            root = Path(temporary)
            registration = root / "registration.bin"
            events = root / "events.bin"
            registration.write_bytes(b"")
            events.write_bytes(
                event_record(99, 1, 2) +
                event_record(202609090901, 8, 9, 3) +
                event_record(202609090901, 9, 10, 3, result=1) +
                event_record(202609090901, 10, 9, 2) +
                event_record(202609090901, 11, 13, flags=1) + b"tail"
            )
            result = subprocess.run(
                ["python3", str(ROOT / "tools/read_persistence_event_log.py"),
                 "--build-id", "202609090901", str(registration), str(events)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["build_id"], 202609090901)
            self.assertEqual(payload["events"][0]["event"], "OperationEntered")
            self.assertEqual(payload["events"][1]["event"], "OperationReturned")
            self.assertEqual(payload["events"][-1]["event"], "QueueOverflow")
            self.assertEqual(payload["missing_returns"], ["LoadData"])
            self.assertTrue(payload["warnings"])
            self.assertIn("末尾", result.stderr)

    def test_reader_rejects_checksum_error_and_reports_missing_return(self):
        with tempfile.TemporaryDirectory(prefix="event-reader-invalid-") as temporary:
            root = Path(temporary)
            registration = root / "registration.bin"
            events = root / "events.bin"
            registration.write_bytes(b"")
            record = bytearray(event_record(7, 1, 9, 1))
            record[60] ^= 0xff
            events.write_bytes(bytes(record))
            result = subprocess.run(
                ["python3", str(ROOT / "tools/read_persistence_event_log.py"),
                 "--build-id", "7", str(registration), str(events)],
                text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("checksum", result.stderr.lower())

    def test_reader_counts_repeated_operation_entries_independently(self):
        with tempfile.TemporaryDirectory(prefix="event-reader-repeated-") as temporary:
            root = Path(temporary)
            registration = root / "registration.bin"
            events = root / "events.bin"
            registration.write_bytes(b"")
            events.write_bytes(
                event_record(7, 1, 9, 1) +
                event_record(7, 2, 10, 1) +
                event_record(7, 3, 9, 1)
            )
            result = subprocess.run(
                ["python3", str(ROOT / "tools/read_persistence_event_log.py"),
                 "--build-id", "7", str(registration), str(events)],
                text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["missing_returns"], ["SaveData"])


if __name__ == "__main__":
    unittest.main()
