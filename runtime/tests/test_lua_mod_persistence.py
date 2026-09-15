import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"


class LuaModPersistenceTests(unittest.TestCase):
    def test_event_diagnostic_wraps_all_mod_persistence_operations_after_validation(self):
        runtime = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "mod_api.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('#include "persistence_event_journal.hpp"', runtime)
        expectations = (
            ("ModSaveData", "SaveData", "ModPersistence::SaveModData"),
            ("ModLoadData", "LoadData", "ModPersistence::LoadModData"),
            ("ModHasData", "HasData", "ModPersistence::HasModData"),
            ("ModRemoveData", "RemoveData", "ModPersistence::RemoveModData"),
        )
        for function, operation, persistence_call in expectations:
            with self.subTest(function=function):
                signature = f"int {function}(lua_State* state) {{"
                body = runtime[runtime.index(signature) + len(signature):]
                body = body[:body.index("\n}\n") + 3]
                entered = "PersistenceEventJournal::Event::OperationEntered"
                returned = "PersistenceEventJournal::Event::OperationReturned"
                self.assertIn(entered, body)
                self.assertIn(returned, body)
                self.assertIn("PersistenceEventJournal::Operation::" + operation, body)
                self.assertLess(body.index(entered), body.index(persistence_call))
                self.assertLess(body.index(persistence_call), body.index(returned))
        self.assertIn("ModPersistence::Result::Missing", runtime)
        self.assertIn("*has = false", (SOURCE / "mod_persistence.hpp").read_text(encoding="utf-8"))

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-persistence-")
        cls.temporary_path = Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_mod_persistence_contract_exists(self):
        header = SOURCE / "mod_persistence.hpp"
        mod_api = SOURCE.parent / "src" / "interfaces" / "lua" / "mod_api.cpp"

        self.assertTrue(header.is_file(), "mod persistence storage is missing")
        source = header.read_text(encoding="utf-8")
        for token in (
            "HashModNamespace",
            "SaveModData",
            "LoadModData",
            "HasModData",
            "RemoveModData",
            "ISMODST2",
            "ConfigureFileApi",
        ):
            self.assertIn(token, source)
        for token in ("SaveData", "LoadData", "HasData", "RemoveData", "ModPersistence"):
            self.assertIn(token, mod_api.read_text(encoding="utf-8"))

    def test_file_api_registration_is_published_before_main_thread_reads_it(self):
        source = (SOURCE / "mod_persistence.hpp").read_text(encoding="utf-8")

        self.assertIn("std::atomic<std::uint32_t> g_fileApiReady{false};", source)
        self.assertIn("g_fileApiReady.load(std::memory_order_acquire)", source)
        self.assertIn("bool IsFileApiReady()", source)
        # 发布点现在在**正式入口** `ConfigureFilePort`（B 把文件能力统一到 IFilePort 这条缝）：
        # 先落提供方、再 release 置位；兼容入口 `ConfigureFileApi` 只是把裸函数包成端口后转调它。
        configure = source[source.index("inline void ConfigureFilePort"):
                           source.index("inline bool IsFileApiReady")]
        self.assertIn("g_fileApiReady.store(port != nullptr, std::memory_order_release);", configure)
        self.assertLess(configure.index("detail::g_filePort = port;"),
                        configure.index("g_fileApiReady.store(port != nullptr"))
        compatibility = source[source.index("inline void ConfigureFileApi"):
                                source.index("inline bool IsFileApiReady")]
        self.assertIn("detail::g_rawFunctions = {open, read, write, close};", compatibility)
        self.assertIn("ConfigureFilePort(&detail::g_rawPort);", compatibility)

    def test_persistence_bridge_registers_file_api_without_terminating_game(self):
        bridge = SOURCE / "program" / "saltynx_external_plugin_persistence_bridge.cpp"
        entry = SOURCE / "program" / "saltynx_external_plugin_persistence_entry.cpp"
        makefile = ROOT / "runtime" / "Makefile"

        self.assertTrue(bridge.is_file(), "production persistence bridge is missing")
        self.assertTrue(entry.is_file(), "production persistence bridge entry is missing")
        self.assertIn("IsaacModRuntime_RegisterSaltyFileApi", bridge.read_text(encoding="utf-8"))
        self.assertIn("SaltySDCore_fopen", bridge.read_text(encoding="utf-8"))
        self.assertIn("RunSaltyNxPersistenceBridge();", entry.read_text(encoding="utf-8"))
        self.assertNotIn("svc #0x26", entry.read_text(encoding="utf-8"))
        self.assertIn("SALTYNX_STAGE144_CPPFILES", makefile.read_text(encoding="utf-8"))
        self.assertIn("mod-persistence-deploy", makefile.read_text(encoding="utf-8"))
        common = (ROOT / "runtime" / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        self.assertIn("filter-out saltynx_external_plugin_persistence_entry.cpp", common)
        runtime_bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        self.assertIn("#if defined(EXL_ENABLE_SALTYNX_DIAGNOSTICS)", runtime_bridge)

    def test_persistence_deployments_require_the_saltynx_file_api_before_callbacks_run(self):
        makefile = (ROOT / "runtime" / "Makefile").read_text(encoding="utf-8")

        self.assertIn("PERSISTENCE_REQUIRED ?= 0", makefile)
        self.assertIn("-DEXL_PERSISTENCE_REQUIRED", makefile)
        for target in (
            "mod-persistence-deploy",
            "mod-persistence-probe-deploy",
            "mod-persistence-probe-trace-deploy",
        ):
            start = makefile.index(target + ":")
            next_target = makefile.find("\n.PHONY:", start + len(target))
            body = makefile[start:next_target if next_target != -1 else len(makefile)]
            with self.subTest(target=target):
                self.assertIn("PERSISTENCE_REQUIRED=1", body)

    def test_hardware_probe_uses_one_pc_mod_to_cover_save_load_and_remove(self):
        probe = ROOT / "runtime" / "diagnostic" / "mod-persistence-probe" / "pc-mods" / "00 Runtime Persistence Probe" / "main.lua"

        self.assertTrue(probe.is_file(), "persistence hardware probe Mod is missing")
        source = probe.read_text(encoding="utf-8")
        for token in ("RegisterMod", "HasData", "SaveData", "LoadData", "RemoveData"):
            self.assertIn(token, source)

    def test_mod_persistence_lua_contract(self):
        header = SOURCE / "mod_persistence.hpp"
        if not header.is_file():
            self.fail("mod persistence storage is missing")

        compatibility = self.temporary_path / "compatibility"
        compatibility.mkdir(exist_ok=True)
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n",
            encoding="utf-8",
        )
        harness = self.temporary_path / "lua_mod_persistence_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "mod_persistence.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"

extern "C" {
#include <lua.h>
}

#include <array>
#include <cstdio>
#include <cstring>

namespace {
std::array<unsigned char, ModPersistence::kMaximumFileSize> g_file{};
std::size_t g_file_size = 0;
bool g_file_exists = false;
bool g_fail_write = false;

void* Open(const char*, const char* mode) {
    if (std::strcmp(mode, "rb") == 0) return g_file_exists ? &g_file : nullptr;
    if (std::strcmp(mode, "wb") != 0) return nullptr;
    g_file_exists = true;
    g_file_size = 0;
    return &g_file;
}

std::size_t Read(void* output, std::size_t size, std::size_t count, void*) {
    const std::size_t requested = size * count;
    const std::size_t actual = requested < g_file_size ? requested : g_file_size;
    std::memcpy(output, g_file.data(), actual);
    return size == 1 ? actual : actual / size;
}

std::size_t Write(const void* input, std::size_t size, std::size_t count, void*) {
    if (g_fail_write) return 0;
    const std::size_t requested = size * count;
    if (requested > g_file.size()) return 0;
    std::memcpy(g_file.data(), input, requested);
    g_file_size = requested;
    return count;
}

int Close(void*) { return 0; }

void Configure() {
    ModPersistence::ResetForTesting();
    ModPersistence::ConfigureFileApi(&Open, &Read, &Write, &Close);
}

void RefreshChecksum() {
    std::uint32_t checksum = 2166136261U;
    for (std::size_t index = 32; index < g_file_size; ++index) {
        checksum ^= g_file[index];
        checksum *= 16777619U;
    }
    for (std::size_t index = 0; index < 4; ++index) {
        g_file[28 + index] = static_cast<unsigned char>(checksum >> (index * 8));
    }
}
}

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::ThunkUnavailable;
}
GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}
GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) {
    return GameIsAscentObservation::MethodUnavailable;
}
GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}
GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}
GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::OpenFailed;
}
}

int main(int argc, char** argv) {
    if (argc != 2) return 90;
    Configure();
    const char* scenario = argv[1];
    const char* script = nullptr;
    if (std::strcmp(scenario, "save") == 0) {
        script = "local m=RegisterMod('Persistent Probe',1); if m:HasData() or m:LoadData()~='' then error('empty') end; m:SaveData('{\"v\":1}'); if not m:HasData() or m:LoadData()~='{\"v\":1}' then error('save') end; m:AddCallback(ModCallbacks.MC_POST_UPDATE,function() end)";
    } else if (std::strcmp(scenario, "load_remove") == 0) {
        const auto key = ModPersistence::HashModNamespace("Persistent Probe", 16);
        if (ModPersistence::SaveModData(key, "previous", 8) != ModPersistence::Result::Success) return 10;
        ModPersistence::ResetCacheForTesting();
        script = "local m=RegisterMod('Persistent Probe',1); if not m:HasData() or m:LoadData()~='previous' then error('load') end; m:RemoveData(); if m:HasData() or m:LoadData()~='' then error('remove') end; m:AddCallback(ModCallbacks.MC_POST_UPDATE,function() end)";
    } else if (std::strcmp(scenario, "invalid") == 0) {
        script = "local m=RegisterMod('Persistent Probe',1); m:SaveData({})";
    } else if (std::strcmp(scenario, "corrupt") == 0) {
        g_file_exists = true;
        g_file_size = 4;
        script = "local m=RegisterMod('Persistent Probe',1); m:HasData()";
    } else if (std::strcmp(scenario, "namespace") == 0) {
        const auto first = ModPersistence::HashModNamespace("First", 5);
        const auto second = ModPersistence::HashModNamespace("Second", 6);
        if (ModPersistence::SaveModData(first, "one", 3) != ModPersistence::Result::Success ||
            ModPersistence::SaveModData(second, "two", 3) != ModPersistence::Result::Success) return 11;
        ModPersistence::ResetCacheForTesting();
        const char* data = nullptr;
        std::size_t length = 0;
        return ModPersistence::LoadModData(first, &data, &length) == ModPersistence::Result::Success &&
                       length == 3 && std::memcmp(data, "one", 3) == 0 &&
                       ModPersistence::LoadModData(second, &data, &length) == ModPersistence::Result::Success &&
                       length == 3 && std::memcmp(data, "two", 3) == 0
                   ? 0
                   : 12;
    } else if (std::strcmp(scenario, "unknown") == 0) {
        const auto key = ModPersistence::HashModNamespace("Persistent Probe", 16);
        if (ModPersistence::SaveModData(key, "old", 3) != ModPersistence::Result::Success) return 13;
        g_file[32 + 16] = 99;
        RefreshChecksum();
        ModPersistence::ResetCacheForTesting();
        if (ModPersistence::SaveModData(key, "new", 3) != ModPersistence::Result::Success) return 14;
        if (g_file_size < 32 || g_file[32 + 16] != 99 || g_file[20] != 2) return 15;
        ModPersistence::ResetCacheForTesting();
        const char* data = nullptr;
        std::size_t length = 0;
        return ModPersistence::LoadModData(key, &data, &length) == ModPersistence::Result::Success &&
                       length == 3 && std::memcmp(data, "new", 3) == 0
                   ? 0
                   : 16;
    } else if (std::strcmp(scenario, "callback_error") == 0) {
        script = "local m=RegisterMod('Persistent Probe',1); m:AddCallback(ModCallbacks.MC_POST_UPDATE,function() error('trace') end)";
    } else if (std::strcmp(scenario, "callback_save_failure") == 0) {
        g_fail_write = true;
        script = "local m=RegisterMod('Persistent Probe',1); m:AddCallback(ModCallbacks.MC_POST_UPDATE,function() m:SaveData('x') end)";
    } else {
        return 91;
    }

    const auto result = LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@persistence.lua");
    if (std::strcmp(scenario, "invalid") == 0 || std::strcmp(scenario, "corrupt") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed ? 0 : 20;
    }
    if (result != LuaRuntime::LuaInitResult::Success) return 21;
    if (std::strcmp(scenario, "callback_error") == 0 ||
        std::strcmp(scenario, "callback_save_failure") == 0) {
        LuaRuntime::DispatchPostUpdate();
        if (!LuaRuntime::TakeCallbackError()) return 23;
        const std::uint64_t detail = LuaRuntime::CallbackFailureDetail();
        const std::uint8_t operation = static_cast<std::uint8_t>(detail >> 8);
        if ((detail & 0xFFU) != LUA_ERRRUN) return 24;
        return operation == (std::strcmp(scenario, "callback_save_failure") == 0 ? 1U : 0U) ? 0 : 25;
    }
    if (std::strcmp(scenario, "save") == 0) {
        ModPersistence::ResetCacheForTesting();
        const auto key = ModPersistence::HashModNamespace("Persistent Probe", 16);
        const char* data = nullptr;
        std::size_t length = 0;
        return ModPersistence::LoadModData(key, &data, &length) == ModPersistence::Result::Success &&
                       length == 7 && std::memcmp(data, "{\"v\":1}", 7) == 0
                   ? 0
                   : 22;
    }
    return 0;
}
'''.lstrip(),
            encoding="utf-8",
        )

        lua_root = SOURCE / "third_party" / "lua-5.3.3" / "src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = self.temporary_path / (lua_source.stem + ".o")
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c", str(lua_source), "-o", str(output)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            lua_objects.append(output)

        binary = self.temporary_path / "lua_mod_persistence_harness"
        build = subprocess.run(
            [
                "c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
                "-DEXL_LAYERED_RUNTIME=1",
                "-DEXL_LOAD_KIND=Module", "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
                "-DEXL_PERSISTENCE_TRACE",
                "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
                "-I", str(lua_root), str(harness),
                *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
                *(str(path) for path in lua_objects), "-lm", "-o", str(binary),
            ],
            text=True,
            capture_output=True,
        )
        self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
        for scenario in (
            "save", "load_remove", "invalid", "corrupt", "namespace", "unknown",
            "callback_error", "callback_save_failure",
        ):
            result = subprocess.run([str(binary), scenario], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, scenario + "\n" + result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
