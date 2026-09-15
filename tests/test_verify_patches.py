from pathlib import Path
import subprocess
import unittest


NRO_PATH = Path(
    "The Binding of Isaac_ Afterbirth+ 1.7.9b "
    "[010021C000B6A800][v524288][UPD]/Program #0/1/.nro/Repentance.nro"
)


class VerifyPatchesTests(unittest.TestCase):
    def test_verifier_accepts_the_supported_repentance_nro(self):
        completed = subprocess.run(
            ["python3", "tools/verify_patches.py", "--nro", str(NRO_PATH)],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("贪婪捐款机补丁: 通过", completed.stdout)
        self.assertIn("普通捐款机补丁: 通过", completed.stdout)


if __name__ == "__main__":
    unittest.main()
