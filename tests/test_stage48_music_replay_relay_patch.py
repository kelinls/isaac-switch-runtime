from pathlib import Path
import re
import unittest

from tools import build_patches as patches
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)


class Stage48MusicReplayRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_patch_rewrites_three_entries_and_preserves_callback_slots(self):
        records = decode_ips(patches.build_stage48_music_replay_relay_patch(self.nro))
        self.assertEqual(records, patches.STAGE48_MUSIC_REPLAY_RELAY_RECORDS)
        self.assertEqual(
            [offset for offset, _ in records],
            [0x427238, 0x68CD40, 0x517CF4, 0x68CD80, 0x517D2C, 0x68CDC0],
        )
        patched = apply_records(self.nro, records)
        self.assertEqual(patched[0x68CD78:0x68CD80], bytes(8))
        self.assertEqual(patched[0x68CDB0:0x68CDB8], bytes(8))
        self.assertEqual(patched[0x68CDF0:0x68CDF8], bytes(8))
        self.assertEqual(patched[0x42723C:0x427248], self.nro[0x42723C:0x427248])
        self.assertEqual(patched[0x517CF8:0x517D04], self.nro[0x517CF8:0x517D04])
        self.assertEqual(patched[0x517D30:0x517D3C], self.nro[0x517D30:0x517D3C])

    def test_music_play_relay_preserves_arguments_and_caller_return_address(self):
        code = patches.STAGE48_MUSIC_PLAY_RELAY_CODE
        self.assertEqual(len(code), 0x40)
        self.assertEqual(
            patches.STAGE48_MUSIC_PLAY_RELAY_SLOT_OFFSET,
            patches.STAGE48_MUSIC_PLAY_RELAY_CODE_OFFSET + 0x38,
        )
        self.assertEqual(
            code[4:20],
            bytes.fromhex(
                "FF8300D1"  # sub sp, sp, #0x20
                "E00700A9"  # stp x0, x1, [sp]
                "FE0B00F9"  # str x30, [sp, #0x10]
                "E01B00BD"  # str s0, [sp, #0x18]
            ),
        )
        self.assertEqual(
            code[0x24:0x34],
            bytes.fromhex(
                "E00740A9"  # ldp x0, x1, [sp]
                "FE0B40F9"  # ldr x30, [sp, #0x10]
                "E01B40BD"  # ldr s0, [sp, #0x18]
                "FF830091"  # add sp, sp, #0x20
            ),
        )

    def test_runtime_verifier_matches_generated_music_relay(self):
        constants = (ROOT / "runtime/source/runtime_constants.hpp").read_text()
        slot = re.search(r"kStage48MusicPlayRelaySlotOffset = (0x[0-9A-Fa-f]+);", constants)
        relay = re.search(
            r"kStage48MusicPlayRelayExpectedBytes = \{(.*?)\n\};",
            constants,
            re.DOTALL,
        )
        self.assertIsNotNone(slot)
        self.assertIsNotNone(relay)
        runtime_bytes = bytes(
            int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", relay.group(1))
        )
        self.assertEqual(int(slot.group(1), 16), patches.STAGE48_MUSIC_PLAY_RELAY_SLOT_OFFSET)
        self.assertEqual(runtime_bytes, patches.STAGE48_MUSIC_PLAY_RELAY_CODE)


if __name__ == "__main__":
    unittest.main()
