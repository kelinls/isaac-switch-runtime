from pathlib import Path
import unittest

from tools.build_patches import (
    GAME_INIT_CONTEXT_OFFSET,
    GAME_INIT_CONTEXT_ORIGINAL,
    GAME_INIT_CALL_ORIGINAL,
    GAME_OBSERVER_RELAY_CODE_OFFSET,
    GAME_OBSERVER_RELAY_RECORDS,
    GAME_OBSERVER_RELAY_SLOT_OFFSET,
    GAME_INIT_CALL_OFFSET,
    GAME_INIT_PLT_OFFSET,
    GAME_UPDATE_CALL_OFFSET,
    GAME_UPDATE_CALL_ORIGINAL,
    GAME_UPDATE_CONTEXT_OFFSET,
    GAME_UPDATE_CONTEXT_ORIGINAL,
    GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET,
    GAME_UPDATE_OBSERVER_RELAY_RECORDS,
    GAME_UPDATE_OBSERVER_RELAY_SLOT_OFFSET,
    GAME_UPDATE_PLT_OFFSET,
    GAME_STATE2_CONTEXT_OFFSET,
    GAME_STATE2_CONTEXT_ORIGINAL,
    GAME_STATE2_CALL_OFFSET,
    GAME_STATE2_CALL_ORIGINAL,
    GAME_STATE2_PLT_OFFSET,
    GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET,
    GAME_STATE2_OBSERVER_RELAY_SLOT_OFFSET,
    GAME_STATE2_OBSERVER_RELAY_RECORDS,
    build_game_observer_relay_patch,
    build_game_update_observer_relay_patch,
    build_game_state2_observer_relay_patch,
    build_game_ispaused_render_observer_relay_patch,
    encode_bl,
)
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class GameObserverRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_relay_only_rewrites_original_game_init_call_and_its_verified_cave(self):
        self.assertEqual(GAME_INIT_CONTEXT_OFFSET, 0x3F5A3C)
        self.assertEqual(GAME_INIT_CALL_OFFSET, 0x3F5A44)
        self.assertEqual(GAME_INIT_CONTEXT_ORIGINAL, bytes.fromhex("E00314AA140100F9"))
        self.assertEqual(GAME_INIT_CALL_ORIGINAL, bytes.fromhex("1B1B0A94"))
        records = decode_ips(build_game_observer_relay_patch(self.nro))

        self.assertEqual(records, GAME_OBSERVER_RELAY_RECORDS)
        self.assertEqual(
            [offset for offset, _ in records],
            [GAME_INIT_CALL_OFFSET, GAME_OBSERVER_RELAY_CODE_OFFSET],
        )
        self.assertEqual(self.nro[GAME_INIT_CONTEXT_OFFSET:GAME_INIT_CALL_OFFSET], GAME_INIT_CONTEXT_ORIGINAL)
        self.assertEqual(self.nro[GAME_INIT_CALL_OFFSET:GAME_INIT_CALL_OFFSET + 4], GAME_INIT_CALL_ORIGINAL)
        self.assertEqual(self.nro[GAME_OBSERVER_RELAY_CODE_OFFSET:GAME_OBSERVER_RELAY_CODE_OFFSET + 0x40], bytes(0x40))

        patched = apply_records(self.nro, records)
        self.assertEqual(patched[GAME_INIT_CONTEXT_OFFSET:GAME_INIT_CALL_OFFSET], GAME_INIT_CONTEXT_ORIGINAL)
        self.assertEqual(patched[GAME_OBSERVER_RELAY_SLOT_OFFSET:GAME_OBSERVER_RELAY_SLOT_OFFSET + 8], bytes(8))
        self.assertEqual(
            patched[GAME_OBSERVER_RELAY_CODE_OFFSET + 0x28:GAME_OBSERVER_RELAY_CODE_OFFSET + 0x2C],
            encode_bl(GAME_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_INIT_PLT_OFFSET),
        )

    def test_relay_rejects_changed_source_identity_guard_or_cave(self):
        changed_source = bytearray(self.nro)
        changed_source[0x100] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            build_game_observer_relay_patch(bytes(changed_source))

        changed_guard = bytearray(self.nro)
        changed_guard[GAME_INIT_CALL_OFFSET] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "Game::Init"):
            build_game_observer_relay_patch(bytes(changed_guard))

        changed_cave = bytearray(self.nro)
        changed_cave[GAME_OBSERVER_RELAY_CODE_OFFSET] = 1
        with self.assertRaisesRegex(ValueError, "Game observer"):
            build_game_observer_relay_patch(bytes(changed_cave))

    def test_game_update_relay_only_rewrites_original_call_and_its_new_verified_cave(self):
        self.assertEqual(GAME_UPDATE_CONTEXT_OFFSET, 0x3F9058)
        self.assertEqual(GAME_UPDATE_CALL_OFFSET, 0x3F905C)
        self.assertEqual(GAME_UPDATE_CONTEXT_ORIGINAL, bytes.fromhex("C00240F9"))
        self.assertEqual(GAME_UPDATE_CALL_ORIGINAL, bytes.fromhex("610E0A94"))
        records = decode_ips(build_game_update_observer_relay_patch(self.nro))

        self.assertEqual(records, GAME_UPDATE_OBSERVER_RELAY_RECORDS)
        self.assertEqual(
            [offset for offset, _ in records],
            [GAME_UPDATE_CALL_OFFSET, GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET],
        )
        self.assertEqual(
            self.nro[GAME_UPDATE_CONTEXT_OFFSET:GAME_UPDATE_CALL_OFFSET],
            GAME_UPDATE_CONTEXT_ORIGINAL,
        )
        self.assertEqual(
            self.nro[GAME_UPDATE_CALL_OFFSET:GAME_UPDATE_CALL_OFFSET + 4],
            GAME_UPDATE_CALL_ORIGINAL,
        )
        self.assertEqual(
            self.nro[
                GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET:
                GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x40
            ],
            bytes(0x40),
        )

        patched = apply_records(self.nro, records)
        self.assertEqual(
            patched[GAME_UPDATE_CONTEXT_OFFSET:GAME_UPDATE_CALL_OFFSET],
            GAME_UPDATE_CONTEXT_ORIGINAL,
        )
        self.assertEqual(
            patched[
                GAME_UPDATE_OBSERVER_RELAY_SLOT_OFFSET:
                GAME_UPDATE_OBSERVER_RELAY_SLOT_OFFSET + 8
            ],
            bytes(8),
        )
        self.assertEqual(
            patched[
                GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x28:
                GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x2C
            ],
            encode_bl(GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_UPDATE_PLT_OFFSET),
        )

    def test_game_update_relay_rejects_changed_source_identity_guard_or_cave(self):
        changed_source = bytearray(self.nro)
        changed_source[0x100] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            build_game_update_observer_relay_patch(bytes(changed_source))

        changed_guard = bytearray(self.nro)
        changed_guard[GAME_UPDATE_CALL_OFFSET] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "Game::Update"):
            build_game_update_observer_relay_patch(bytes(changed_guard))

        changed_cave = bytearray(self.nro)
        changed_cave[GAME_UPDATE_OBSERVER_RELAY_CODE_OFFSET] = 1
        with self.assertRaisesRegex(ValueError, "Game update observer"):
            build_game_update_observer_relay_patch(bytes(changed_cave))

    def test_game_state2_relay_only_rewrites_original_call_and_its_verified_cave(self):
        self.assertEqual(GAME_STATE2_CONTEXT_OFFSET, 0x3F9038)
        self.assertEqual(GAME_STATE2_CALL_OFFSET, 0x3F903C)
        self.assertEqual(GAME_STATE2_CONTEXT_ORIGINAL, bytes.fromhex("C00240F9"))
        self.assertEqual(GAME_STATE2_CALL_ORIGINAL, bytes.fromhex("610E0A94"))
        records = decode_ips(build_game_state2_observer_relay_patch(self.nro))

        self.assertEqual(records, GAME_STATE2_OBSERVER_RELAY_RECORDS)
        self.assertEqual(
            [offset for offset, _ in records],
            [GAME_STATE2_CALL_OFFSET, GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET],
        )
        self.assertEqual(
            self.nro[GAME_STATE2_CONTEXT_OFFSET:GAME_STATE2_CALL_OFFSET],
            GAME_STATE2_CONTEXT_ORIGINAL,
        )
        self.assertEqual(
            self.nro[GAME_STATE2_CALL_OFFSET:GAME_STATE2_CALL_OFFSET + 4],
            GAME_STATE2_CALL_ORIGINAL,
        )
        self.assertEqual(
            self.nro[
                GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET:
                GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x40
            ],
            bytes(0x40),
        )

        patched = apply_records(self.nro, records)
        self.assertEqual(
            patched[
                GAME_STATE2_OBSERVER_RELAY_SLOT_OFFSET:
                GAME_STATE2_OBSERVER_RELAY_SLOT_OFFSET + 8
            ],
            bytes(8),
        )
        self.assertEqual(
            patched[
                GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x28:
                GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x2C
            ],
            encode_bl(GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_STATE2_PLT_OFFSET),
        )

    def test_game_state2_relay_rejects_changed_source_identity_guard_or_cave(self):
        changed_source = bytearray(self.nro)
        changed_source[0x100] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "source_sha256"):
            build_game_state2_observer_relay_patch(bytes(changed_source))

        changed_guard = bytearray(self.nro)
        changed_guard[GAME_STATE2_CALL_OFFSET] ^= 0xFF
        with self.assertRaisesRegex(ValueError, "state-2"):
            build_game_state2_observer_relay_patch(bytes(changed_guard))

        changed_cave = bytearray(self.nro)
        changed_cave[GAME_STATE2_OBSERVER_RELAY_CODE_OFFSET] = 1
        with self.assertRaisesRegex(ValueError, "state-2"):
            build_game_state2_observer_relay_patch(bytes(changed_cave))

    def test_ispaused_render_relay_only_rewrites_the_original_call_and_new_cave(self):
        from tools.build_patches import (
            GAME_ISPAUSED_RENDER_CALL_OFFSET,
            GAME_ISPAUSED_RENDER_CALL_ORIGINAL,
            GAME_ISPAUSED_RENDER_CONTEXT_OFFSET,
            GAME_ISPAUSED_RENDER_CONTEXT_ORIGINAL,
            GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET,
            GAME_ISPAUSED_RENDER_OBSERVER_RELAY_SLOT_OFFSET,
            GAME_ISPAUSED_RENDER_RELAY_RECORDS,
            GAME_ISPAUSED_RENDER_PLT_OFFSET,
        )
        self.assertEqual(GAME_ISPAUSED_RENDER_CONTEXT_OFFSET, 0x342998)
        self.assertEqual(GAME_ISPAUSED_RENDER_CALL_OFFSET, 0x3429A4)
        self.assertEqual(GAME_ISPAUSED_RENDER_CALL_ORIGINAL, bytes.fromhex("D7B90C94"))
        self.assertEqual(self.nro[GAME_ISPAUSED_RENDER_CONTEXT_OFFSET:GAME_ISPAUSED_RENDER_CALL_OFFSET], GAME_ISPAUSED_RENDER_CONTEXT_ORIGINAL)
        records = decode_ips(build_game_ispaused_render_observer_relay_patch(self.nro))
        self.assertEqual(records, GAME_ISPAUSED_RENDER_RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [GAME_ISPAUSED_RENDER_CALL_OFFSET, GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET])
        patched = apply_records(self.nro, records)
        self.assertEqual(patched[GAME_ISPAUSED_RENDER_OBSERVER_RELAY_SLOT_OFFSET:GAME_ISPAUSED_RENDER_OBSERVER_RELAY_SLOT_OFFSET + 8], bytes(8))
        self.assertEqual(patched[GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x28:GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x2C], encode_bl(GAME_ISPAUSED_RENDER_OBSERVER_RELAY_CODE_OFFSET + 0x28, GAME_ISPAUSED_RENDER_PLT_OFFSET))


if __name__ == "__main__":
    unittest.main()
