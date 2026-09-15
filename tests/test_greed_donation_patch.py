from pathlib import Path
import subprocess
import unittest

from tools.build_patches import GREED_RECORD, build_greed_patch
from tools.nro_ips import apply_records, decode_ips


NRO_PATH = Path(
    "The Binding of Isaac_ Afterbirth+ 1.7.9b "
    "[010021C000B6A800][v524288][UPD]/Program #0/1/.nro/Repentance.nro"
)


class GreedDonationPatchTests(unittest.TestCase):
    def test_build_script_runs_directly(self):
        completed = subprocess.run(
            ["python3", "tools/build_patches.py", "--help"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("生成以撒忏悔版捐款机 IPS 补丁", completed.stdout)

    def test_greed_patch_replaces_only_break_chance_function_entry(self):
        original = NRO_PATH.read_bytes()

        records = decode_ips(build_greed_patch(original))

        self.assertEqual(records, [GREED_RECORD])
        self.assertEqual(
            original[0x2FD1BC:0x2FD1C4], bytes.fromhex("FD7BBEA9F30B00F9")
        )
        self.assertEqual(
            apply_records(original, records)[0x2FD1BC:0x2FD1C4],
            bytes.fromhex("E003271EC0035FD6"),
        )


if __name__ == "__main__":
    unittest.main()
