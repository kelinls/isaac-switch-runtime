from pathlib import Path
import base64
import re
import subprocess
import unittest

from tools import build_patches as patches
from tools.nro_ips import apply_records, decode_ips


ROOT = Path(__file__).resolve().parents[1]
NRO = (
    ROOT
    / "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    / "Program #0/1/.nro/Repentance.nro"
)
TOOLCHAIN = "/opt/devkitpro/devkitA64/bin"


class PreGetCollectibleRelayPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.nro = NRO.read_bytes()

    def test_builder_exposes_a_version_locked_pre_get_collectible_relay(self):
        self.assertTrue(callable(getattr(patches, "build_pre_get_collectible_relay_patch", None)))

    def test_patch_preserves_original_trampoline_and_a_zero_callback_slot(self):
        records = decode_ips(patches.build_pre_get_collectible_relay_patch(self.nro))
        self.assertEqual(records, patches.PRE_GET_COLLECTIBLE_RELAY_RECORDS)
        self.assertEqual([offset for offset, _ in records], [0x3C6350, 0x68CE00])
        patched = apply_records(self.nro, records)
        self.assertEqual(patched[0x3C6354:0x3C6360], self.nro[0x3C6354:0x3C6360])
        self.assertEqual(patched[0x68CE58:0x68CE60], bytes(8))

    def test_relay_executes_original_frame_then_preserves_abi_on_both_return_paths(self):
        code = patches.PRE_GET_COLLECTIBLE_RELAY_CODE
        self.assertEqual(len(code), 0x60)
        self.assertEqual(code[:0x18], bytes.fromhex(
            "FF0304D1"  # original: sub sp, sp, #0x100
            "FF0301D1"  # relay frame: sub sp, sp, #0x40
            "E00700A9"  # stp x0, x1, [sp]
            "E20F01A9"  # stp x2, x3, [sp, #0x10]
            "E41300F9"  # str x4, [sp, #0x20]
            "FE1700F9"  # str x30, [sp, #0x28]
        ))
        self.assertEqual(code[0x18:0x30], bytes.fromhex(
            "11020010"  # adr x17, slot
            "30FEDFC8"  # ldar x16, [x17]
            "100100B4"  # cbz x16, original trampoline
            "00023FD6"  # blr x16
            "11FC60D3"  # lsr x17, x0, #32
            "B10000B4"  # cbz x17, nil -> original trampoline
        ))
        self.assertEqual(code[0x30:0x40], bytes.fromhex(
            "FE1740F9"  # restore caller x30
            "FF030191"  # discard relay frame
            "FF030491"  # discard original frame
            "C0035FD6"  # ret with callback's w0 override
        ))
        self.assertEqual(code[0x40:0x54], bytes.fromhex(
            "E00740A9"  # restore x0/x1
            "E20F41A9"  # restore x2/x3
            "E41340F9"  # restore x4
            "FE1740F9"  # restore caller x30
            "FF030191"  # discard relay frame
        ))
        self.assertEqual(
            code[0x54:0x58],
            patches.encode_branch(0x68CE54, 0x3C6354),
        )
        self.assertEqual(code[0x58:], bytes(8))

    def test_generated_machine_code_disassembles_with_the_required_stack_and_control_flow(self):
        source = """.text
relay:
  sub sp, sp, #0x100
  sub sp, sp, #0x40
  stp x0, x1, [sp]
  stp x2, x3, [sp, #0x10]
  str x4, [sp, #0x20]
  str x30, [sp, #0x28]
  adr x17, slot
  ldar x16, [x17]
  cbz x16, original
  blr x16
  lsr x17, x0, #32
  cbz x17, original
  ldr x30, [sp, #0x28]
  add sp, sp, #0x40
  add sp, sp, #0x100
  ret
original:
  ldp x0, x1, [sp]
  ldp x2, x3, [sp, #0x10]
  ldr x4, [sp, #0x20]
  ldr x30, [sp, #0x28]
  add sp, sp, #0x40
slot:
  .xword 0
"""
        result = subprocess.run(
            [
                "docker", "run", "--rm", "-i", "devkitpro/devkita64:latest", "sh", "-lc",
                f"{TOOLCHAIN}/aarch64-none-elf-as -o /tmp/relay.o - && "
                f"{TOOLCHAIN}/aarch64-none-elf-objdump -d /tmp/relay.o",
            ],
            cwd=ROOT, input=source, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        for instruction in (
            "sub\tsp, sp, #0x100", "sub\tsp, sp, #0x40", "stp\tx0, x1, [sp]",
            "stp\tx2, x3, [sp, #16]", "str\tx4, [sp, #32]", "str\tx30, [sp, #40]",
            "ldar\tx16, [x17]", "blr\tx16", "lsr\tx17, x0, #32", "ret",
            "ldp\tx0, x1, [sp]", "ldp\tx2, x3, [sp, #16]",
        ):
            self.assertIn(instruction, output)

    def test_final_relay_payload_disassembles_as_the_version_locked_contract(self):
        payload = base64.b64encode(patches.PRE_GET_COLLECTIBLE_RELAY_CODE).decode("ascii")
        result = subprocess.run(
            [
                "docker", "run", "--rm", "-i", "devkitpro/devkita64:latest", "sh", "-lc",
                f"base64 -d > /tmp/relay.bin && {TOOLCHAIN}/aarch64-none-elf-objdump "
                "-D -b binary -m aarch64 --adjust-vma=0x68ce00 /tmp/relay.bin",
            ],
            cwd=ROOT, input=payload, text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        for instruction in (
            r"68ce00:\s+d10403ff\s+sub\s+sp, sp, #0x100",
            r"68ce18:\s+10000211\s+adr\s+x17, 0x68ce58",
            r"68ce20:\s+b4000110\s+cbz\s+x16, 0x68ce40",
            r"68ce2c:\s+b40000b1\s+cbz\s+x17, 0x68ce40",
            r"68ce3c:\s+d65f03c0\s+ret",
            r"68ce54:\s+17f4e540\s+b\s+0x3c6354",
        ):
            self.assertRegex(output, re.compile(instruction))


if __name__ == "__main__":
    unittest.main()
