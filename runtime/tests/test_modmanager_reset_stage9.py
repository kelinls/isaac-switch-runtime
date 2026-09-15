import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class ModManagerResetStage9Tests(unittest.TestCase):
    def test_stage9_reset_constants_are_version_bound(self):
        text = (SOURCE / "runtime_constants.hpp").read_text()
        self.assertRegex(text, r"kModManagerResetFileOffset\s*=\s*0x422440")
        part = re.search(r"kModManagerResetExpectedBytes = \{(.*?)\};", text, re.S)
        self.assertIsNotNone(part)
        actual = bytes(int(value, 16) for value in re.findall(r"0x([0-9A-Fa-f]{2})", part.group(1)))
        self.assertEqual(actual, bytes.fromhex("FD7BBEA9F44F01A9FD030091F30300AA"))

    def test_stage9_callback_invokes_reset_before_success_break(self):
        source = (SOURCE / "hook_manager.cpp").read_text()
        match = re.search(
            r"NORETURN void ManagerLoadConfigsResetDiagnosticCallback.*?svcExitProcess\(\);\n}",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        callback = match.group(0)
        for required in (
            "self == nullptr",
            "UINTPTR_MAX - kManagerToModManagerOffset",
            "candidate & 7u",
            "using ResetFn = void (*)(void*)",
            "reset(reinterpret_cast<void*>(candidate))",
            "0x4953414143525354ULL",
            "(9ULL << 32) | 0x422440ULL",
        ):
            with self.subTest(required=required):
                self.assertIn(required, callback)
        self.assertLess(
            callback.index("reset(reinterpret_cast<void*>(candidate))"),
            callback.index("0x4953414143525354ULL"),
        )
        for forbidden in ("Orig(", "TryRedirectPath", "LuaRuntime", "RuntimeFs", "return;"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, callback)

    def test_stage9_installer_validates_reset_before_callback_publish(self):
        source = (SOURCE / "hook_manager.cpp").read_text()
        installer_name = "TryInstallManagerLoadConfigsResetDiagnostic"
        self.assertIn(installer_name, source)
        install = source[source.index(installer_name):]
        for required in (
            "VerifyManagerLoadConfigs",
            "VerifyManagerLoadConfigsRelay",
            "IsMappedRxModuleCodeWindow(resetAddress, kModManagerResetExpectedBytes.size())",
            "verify_bytes(kModManagerResetExpectedBytes.data()",
            "__atomic_load_n",
            "__atomic_store_n",
            "ManagerLoadConfigsResetDiagnosticCallback",
        ):
            with self.subTest(required=required):
                self.assertIn(required, install)

    def test_stage9_entry_installs_only_the_reset_diagnostic(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        stage = re.search(
            r"#elif defined\(EXL_DIAGNOSTIC_STAGE\) && EXL_DIAGNOSTIC_STAGE == 9\n"
            r"(?P<body>\s*const LoadConfigsResetDiagnosticInstallResult.*?)(?=#elif defined\(EXL_DIAGNOSTIC_STAGE\) && EXL_DIAGNOSTIC_STAGE == 8)",
            source,
            re.S,
        )
        self.assertIsNotNone(stage)
        body = stage.group("body")
        self.assertIn("TryInstallManagerLoadConfigsResetDiagnostic", body)
        self.assertNotIn("LuaRuntime::Initialize", body)
        self.assertNotIn("TryInstallManagerUpdateHook", body)

    def test_stage9_selects_the_full_runtime_entry(self):
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text()
        self.assertIn(
            "EXL_DIAGNOSTIC_STAGE != 6 && EXL_DIAGNOSTIC_STAGE != 7 && "
            "EXL_DIAGNOSTIC_STAGE != 8 && EXL_DIAGNOSTIC_STAGE != 9 && "
            "EXL_DIAGNOSTIC_STAGE != 11",
            selector,
        )
        self.assertIn('#include "../runtime_entry.cpp"', selector)

    def test_stage9_build_and_documentation_are_isolated(self):
        makefile = (ROOT / "Makefile").read_text()
        readme = (ROOT / "README.md").read_text()
        self.assertIn("0 1 2 3 4 5 6 7 8 9", makefile)
        self.assertIn("filter 8 9,$(DIAGNOSTIC_STAGE)", makefile)
        for marker in ("| 9 |", "ISAACRST", "ISAACRFL", "0x0000000900422440", "不得用于正常游玩"):
            with self.subTest(marker=marker):
                self.assertIn(marker, readme)


if __name__ == "__main__":
    unittest.main()
