import unittest
from pathlib import Path

try:
    from .test_support import makefile_accepted_diagnostic_stages
except ImportError:
    from test_support import makefile_accepted_diagnostic_stages


ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "runtime"
SOURCE = RUNTIME / "source"


class Stage118ManagedFileWriteDiagnosticTests(unittest.TestCase):
    def test_stage118_is_a_single_game_file_write_without_lua_or_save_manager_commit(self):
        makefile = (RUNTIME / "Makefile").read_text(encoding="utf-8")
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "runtime_entry.cpp").read_text(encoding="utf-8")
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        reader = (SOURCE / "game_file_reader.cpp").read_text(encoding="utf-8")

        # 见 `makefile_accepted_diagnostic_stages` 的注释：按阶段号集合判成员。
        self.assertIn(118, makefile_accepted_diagnostic_stages(makefile))
        self.assertIn("EXL_DIAGNOSTIC_STAGE != 118", selector)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 118", entry)
        self.assertIn("TryInstallStage118ManagedFileWriteDiagnostic", entry)
        self.assertIn("ReportStage118Failure", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 118", hook)
        self.assertIn('#include "game_file_reader.hpp"', hook)
        self.assertIn("RunStage118ManagedFileWriteDiagnostic", hook)
        self.assertIn("WriteDiagnosticFile", reader)
        self.assertIn("save:/isaac_mod_stage118.bin", reader)
        self.assertIn("ISAAC_STAGE118!", reader)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 118)", reader)

        run_start = hook.index("NORETURN void RunStage118ManagedFileWriteDiagnostic")
        run_section = hook[run_start:hook.index("#endif", run_start)]
        callback_start = hook.index("HOOK_DEFINE_TRAMPOLINE(ManagerUpdateHook)")
        callback_end = hook.index("#if !defined(EXL_DIAGNOSTIC_STAGE)", callback_start)
        callback_section = hook[callback_start:callback_end]
        self.assertIn("Orig(self);", callback_section)
        self.assertIn("RunStage118ManagedFileWriteDiagnostic", callback_section)
        self.assertIn("ReportStage118Success", run_section)
        self.assertNotIn("LuaRuntime", run_section)
        self.assertNotIn("commit_save_data", run_section)
        self.assertNotIn("GetTreasureRoomVisitCount", run_section)

    def test_stage118_uses_the_full_wrapped_file_lifecycle_and_reports_each_failure_boundary(self):
        reader = (SOURCE / "game_file_reader.cpp").read_text(encoding="utf-8")
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        section = reader[reader.index("WriteDiagnosticResult WriteDiagnosticFile"):]

        for token in ("construct(fileStorage.data())", "openWrite(fileStorage.data()", "write(fileStorage.data()",
                      "close(fileStorage.data())", "destroy(fileStorage.data())"):
            self.assertIn(token, section)
        self.assertIn("kGameFileOpenWriteFileOffset", constants)
        self.assertIn("kGameFileWriteFileOffset", constants)
        self.assertIn("kGameFileOpenWriteExpectedBytes", constants)
        self.assertIn("kGameFileWriteExpectedBytes", constants)
        self.assertIn("template <std::size_t ExpectedSize>", reader)
        for result in ("OpenFailed", "WriteMismatch", "Success"):
            self.assertIn(f"WriteDiagnosticResult::{result}", reader)


if __name__ == "__main__":
    unittest.main()
