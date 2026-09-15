import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class ManagerLoadConfigsStage8Tests(unittest.TestCase):
    def test_stage8_constants_bind_the_second_relay_to_the_verified_offsets(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text()

        for name, value in (
            ("kManagerLoadConfigsFileOffset", "0x3F5C38"),
            ("kManagerToModManagerOffset", "0x36800"),
            ("kManagerLoadConfigsRelayCodeOffset", "0x68CC20"),
            ("kManagerLoadConfigsRelayFallbackOffset", "0x68CC30"),
            ("kManagerLoadConfigsRelaySlotOffset", "0x68CC38"),
        ):
            with self.subTest(name=name):
                self.assertRegex(constants, rf"{name}\s*=\s*{value}")

        self.assertIn("0xFA, 0x5B, 0x0A, 0x14", constants)
        self.assertIn("0xFF, 0x43, 0x02, 0xD1", constants)

    def test_stage8_callback_only_validates_the_manager_member_address(self):
        source = (SOURCE / "hook_manager.cpp").read_text()
        guarded = re.search(
            r"#if defined\(EXL_DIAGNOSTIC_STAGE\) && EXL_DIAGNOSTIC_STAGE == 8\b"
            r"(?P<body>.*?)#endif",
            source,
            re.DOTALL,
        )
        self.assertIsNotNone(guarded)
        body = guarded.group("body")
        callback = body[body.index("ManagerLoadConfigsDiagnosticCallback"):]

        self.assertIn("self == nullptr", callback)
        self.assertIn("UINTPTR_MAX - kManagerToModManagerOffset", callback)
        self.assertIn("candidate & 7u", callback)
        self.assertIn("0x49534141434D4F44ULL", callback)
        self.assertIn("0x49534141434D464CULL", callback)
        self.assertIn("(8ULL << 32) | 0x36800ULL", callback)
        for forbidden in ("Orig(", "Reset", "TryRedirectPath", "LuaRuntime", "RuntimeFs", "*modManager"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, callback)

    def test_stage8_installer_verifies_and_publishes_only_the_second_slot(self):
        source = (SOURCE / "hook_manager.cpp").read_text()
        install = source[
            source.index("LoadConfigsDiagnosticInstallResult TryInstallManagerLoadConfigsDiagnostic"):
            source.index("bool InstallManagerUpdateHook")
        ]
        verifier = source[
            source.index("bool VerifyManagerLoadConfigsRelay"):
            source.index("HookInstallResult TryInstallManagerUpdateHook")
        ]

        self.assertIn("VerifyManagerLoadConfigs", install)
        self.assertIn("VerifyManagerLoadConfigsRelay", install)
        self.assertIn("kManagerLoadConfigsRelaySlotOffset", verifier)
        self.assertNotIn("kManagerRelaySlotOffset", install)
        self.assertIn("RwPages slotPages", install)
        self.assertIn("__atomic_load_n", install)
        self.assertIn("__ATOMIC_ACQUIRE", install)
        self.assertIn("__atomic_store_n", install)
        self.assertIn("__ATOMIC_RELEASE", install)
        self.assertIn("slotPages.Flush()", install)
        self.assertLess(install.index("__atomic_load_n"), install.index("__atomic_store_n"))
        self.assertLess(install.index("__atomic_store_n"), install.index("slotPages.Flush()"))

    def test_stage8_entry_installs_loadconfigs_relay_without_lua_initialization(self):
        source = (SOURCE / "runtime_entry.cpp").read_text()
        worker_start = source.index("void ModuleWorker")
        branch_start = source.index(
            "#elif defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 8\n"
            "            const LoadConfigsDiagnosticInstallResult install =",
            worker_start,
        )
        body = source[branch_start:source.index("#else\n#if !defined(EXL_DIAGNOSTIC_STAGE)", branch_start)]
        self.assertIn("TryInstallManagerLoadConfigsDiagnostic", body)
        self.assertIn("ReportStage8Failure", source)
        self.assertNotIn("LuaRuntime::Initialize", body)
        self.assertNotIn("TryInstallManagerUpdateHook", body)


if __name__ == "__main__":
    unittest.main()
