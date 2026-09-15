import hashlib
import unittest
from pathlib import Path

from tools import build_patches as patches


ROOT = Path(__file__).resolve().parents[1]
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)


def decode_ips(payload: bytes) -> list[tuple[int, bytes]]:
    assert payload[:5] == b"PATCH"
    offset = 5
    records = []
    while payload[offset:offset + 3] != b"EOF":
        position = int.from_bytes(payload[offset:offset + 3], "big")
        length = int.from_bytes(payload[offset + 3:offset + 5], "big")
        offset += 5
        records.append((position, payload[offset:offset + length]))
        offset += length
    return records


class Stage102ChangeRoomRelayPatchTests(unittest.TestCase):
    def setUp(self):
        self.nro = NRO.read_bytes()
        self.assertEqual(hashlib.sha256(self.nro).hexdigest(), patches.EXPECTED_SOURCE_SHA256)

    def test_patch_only_rewrites_the_original_level_call_and_its_dedicated_cave(self):
        records = decode_ips(patches.build_game_change_room_relay_patch(self.nro))
        self.assertEqual(
            records,
            [
                (patches.GAME_CHANGE_ROOM_CALL_OFFSET, patches.GAME_CHANGE_ROOM_RELAY_TARGET_RECORD[1]),
                (patches.GAME_CHANGE_ROOM_RELAY_CODE_OFFSET, patches.GAME_CHANGE_ROOM_RELAY_CODE),
            ],
        )
        self.assertEqual(records[0][1], patches.encode_branch(
            patches.GAME_CHANGE_ROOM_CALL_OFFSET, patches.GAME_CHANGE_ROOM_RELAY_CODE_OFFSET,
        ))
        self.assertEqual(records[1][1][8:12], patches.encode_bl(
            patches.GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 8,
            patches.GAME_CHANGE_ROOM_LEVEL_CHANGE_ROOM_PLT_OFFSET,
        ))
        self.assertEqual(records[1][1][40:44], patches.encode_branch(
            patches.GAME_CHANGE_ROOM_RELAY_CODE_OFFSET + 0x28,
            patches.GAME_CHANGE_ROOM_CALL_OFFSET + 4,
        ))

    def test_empty_callback_path_restores_lr_and_stack_before_resuming_game(self):
        relay = patches.GAME_CHANGE_ROOM_RELAY_CODE
        # cbz x16 at +0x18 must skip only `blr x16` and land on the common
        # epilogue at +0x20; a zero slot is possible before Runtime publishes.
        self.assertEqual(relay[0x18:0x1C], bytes.fromhex("500000B4"))
        self.assertEqual(relay[0x20:0x24], bytes.fromhex("FE0740F9"))
        self.assertEqual(relay[0x24:0x28], bytes.fromhex("FF430091"))

    def test_patch_rejects_changed_callsite_or_nonempty_cave(self):
        changed_call = bytearray(self.nro)
        changed_call[patches.GAME_CHANGE_ROOM_CALL_OFFSET] ^= 1
        with self.assertRaises(ValueError):
            patches.build_game_change_room_relay_patch(bytes(changed_call))

        changed_cave = bytearray(self.nro)
        changed_cave[patches.GAME_CHANGE_ROOM_RELAY_CODE_OFFSET] = 1
        with self.assertRaises(ValueError):
            patches.build_game_change_room_relay_patch(bytes(changed_cave))


if __name__ == "__main__":
    unittest.main()
