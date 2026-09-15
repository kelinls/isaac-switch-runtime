import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class EmbeddedLuaRuntimeBoundaryTests(unittest.TestCase):
    def test_vendor_lua_is_pinned_to_5_3_3_and_excludes_unsafe_libraries(self):
        lua_root = SOURCE / "third_party" / "lua-5.3.3"
        lua_header = lua_root / "src" / "lua.h"
        self.assertTrue(lua_header.is_file(), "Lua 5.3.3 source is not vendored")
        self.assertTrue((lua_root / "COPYRIGHT").is_file(), "Lua license is missing")
        source_manifest = lua_root / "SOURCE.md"
        self.assertTrue(source_manifest.is_file(), "Lua source provenance is missing")
        self.assertIn("5113c06884f7de453ce57702abaac1d618307f33f6789fa870e87a59d772aca2",
                      source_manifest.read_text())
        lua_header_text = lua_header.read_text()
        self.assertIn('#define LUA_VERSION_MAJOR\t"5"', lua_header_text)
        self.assertIn('#define LUA_VERSION_MINOR\t"3"', lua_header_text)
        self.assertIn('#define LUA_VERSION_RELEASE\t"3"', lua_header_text)

        rules = (ROOT / "misc" / "mk" / "common.mk").read_text()
        self.assertIn("third_party/lua-5.3.3/src", rules)
        for source in ("lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"):
            with self.subTest(source=source):
                self.assertIn(source, rules)
        self.assertNotIn("luaL_openlibs", (SOURCE / "lua_runtime.cpp").read_text())

    def test_lua_runtime_declares_worker_to_game_thread_boundary(self):
        header_path = SOURCE / "lua_runtime.hpp"
        self.assertTrue(header_path.is_file(), "Lua Runtime public boundary is missing")
        header = header_path.read_text()
        for token in (
            "enum class LuaInitResult",
            "LuaInitResult Initialize()",
            "void DispatchPostUpdate()",
            "bool IsReady()",
            "u32 PostUpdateCount()",
            "bool TakeCallbackError()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, header)

    def test_lua_game_api_declares_a_pointer_free_handle_boundary(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        handles = (SOURCE / "lua_object_handles.hpp").read_text()
        game_api = (
            SOURCE.parent / "src" / "interfaces" / "lua" / "game_api.cpp"
        ).read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()

        self.assertIn("void SetGameBindings(uintptr_t ownerSlot, uintptr_t isPausedThunk);", header)
        self.assertIn('constexpr char kGameMetatable[] = "IsaacRuntime.Game";', handles)
        self.assertIn("struct GameHandle", handles)
        self.assertIn("lua_newuserdata(state, sizeof(GameHandle))", runtime)
        self.assertIn("lua_setglobal(state, \"Game\")", runtime)
        handle = handles[handles.index("struct GameHandle"):handles.index("struct LevelHandle")]
        for forbidden in ("Game*", "ownerSlot", "isPausedThunk", "uintptr_t"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, handle)

    def test_lua_runtime_can_initialize_from_a_memory_chunk(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        self.assertIn("LuaInitResult InitializeFromBuffer(const char* script, std::size_t length, const char* chunkName);", header)
        self.assertIn("LuaInitResult InitializeFromBuffer", runtime)
        self.assertIn('luaL_loadbufferx(state, script, length, chunkName, "t")', runtime)
        self.assertIn("script == nullptr || length == 0 || chunkName == nullptr", runtime)
        validation = runtime[runtime.index("script == nullptr || length == 0 || chunkName == nullptr"):]
        self.assertIn("return LuaInitResult::ScriptLoadFailed;", validation[:200])

    def test_stage13_uses_pc_post_update_contract(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()

        self.assertIn("InitializeStage13Mod(const char* entry, std::size_t entryLength", header)
        self.assertIn("u32 RequireFailureDetail();", header)
        for expected in (
            "lua_pushcfunction(state, Require)",
            '"require"',
            "kStage13PostUpdateCallback = kStage13CallbackValue",
            "lua_pushvalue(state, g_ModObjectRef)",
            "moduleName[index] == '.'",
            "moduleName[index] == '/' || moduleName[index] == '\\\\'",
            "kStage13RequireMaximumDepth = 8",
            'luaL_loadbufferx(state, source.data(), length, chunkName, "t")',
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, runtime)

        self.assertIn("constexpr int kPostUpdateCallback = 1", runtime)
        self.assertNotIn("struct CallbackRecord", runtime)
        self.assertNotIn("g_PostUpdateCallback", runtime)
        self.assertIn(
            "lua_rawgeti(g_LuaState, LUA_REGISTRYINDEX, descriptor.modReference)",
            runtime,
        )
        # `POST_UPDATE` 仍然只收到 Mod 对象**一个**实参：`DispatchPostUpdate` 用
        # `LuaCallbackInvoker invoker{0, false, true}`（无 manager、无第二个实参）构造调用者，
        # 于是调用点的实参个数表达式落到 `hasSecondArgument ? 2 : 1` 的 1 那一支。
        #
        # 原断言写死了 `lua_pcallk(g_LuaState, 1, 0, 0, 0, nullptr)`。第二个实参是为
        # `MC_POST_GAME_STARTED` 加的（EID 的处理函数签名是 `(mod, isSave)`），
        # 调用点因而改成条件表达式，所以这里改为分别钉住"POST_UPDATE 那条调用者不带第二实参"
        # 与"调用点的实参个数表达式"两件事，而不是放宽断言。
        self.assertIn("LuaCallbackInvoker invoker{0, false, true};", runtime)
        self.assertIn("lua_pcallk(g_LuaState, hasSecondArgument ? 2 : 1, 0, 0, 0, nullptr)", runtime)

    def test_lua_runtime_is_included_by_the_program_source_module(self):
        wrapper = SOURCE / "program" / "lua_runtime.cpp"
        self.assertTrue(wrapper.is_file(), "Lua Runtime is not compiled by the program source module")
        self.assertEqual(wrapper.read_text(), '#include "../lua_runtime.cpp"\n')

    def test_embedded_script_uses_only_the_minimum_pc_style_callback_api(self):
        script = (SOURCE / "program" / "embedded_lua_test_script.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        # `Mod:AddCallback` lives in the Mod family unit since the migration, so the
        # tokens it owns are checked there rather than in the Runtime core.
        mod_api = (SOURCE.parent / "src" / "interfaces" / "lua" / "mod_api.cpp").read_text()

        self.assertIn('RegisterMod("EmbeddedRuntimeTest", 1)', script)
        self.assertIn("mod:AddCallback(ModCallbacks.MC_POST_UPDATE", script)
        self.assertIn("RuntimeTest.MarkPostUpdate()", script)
        for token in (
            "luaL_newstate", "luaL_loadbufferx", "lua_pcallk", "luaL_newmetatable",
            "RegisterMod", "MC_POST_UPDATE", "MarkPostUpdate",
            "luaL_checkstring", "luaL_ref",
        ):
            with self.subTest(token=token):
                self.assertIn(token, runtime)
        for token in ("AddCallback", "luaL_checkinteger", "luaL_checktype"):
            with self.subTest(token=token, unit="mod_api.cpp"):
                self.assertIn(token, mod_api)

    def test_lua_runtime_opens_only_explicit_safe_standard_libraries(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        for token in (
            "luaopen_base", "luaopen_coroutine", "luaopen_table", "luaopen_string",
            "luaopen_math", "luaopen_utf8",
        ):
            with self.subTest(token=token):
                self.assertIn(token, runtime)
        for forbidden in ("luaopen_io", "luaopen_os", "luaopen_package", "luaopen_debug", "luaopen_bit32"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, runtime)

    def test_lua_runtime_protects_library_and_api_registration_from_lua_errors(self):
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        self.assertIn("int PrepareRuntime", runtime)
        preparation = runtime[runtime.index("int PrepareRuntime"):runtime.index("LuaInitResult ResetAfterFailure")]
        initialize = runtime[runtime.index("LuaInitResult InitializeScript"):runtime.index("void DispatchPostUpdate()")]

        self.assertIn("OpenSafeLibraries(state);", preparation)
        self.assertIn("RegisterModApi(state);", preparation)
        self.assertIn("lua_pushcfunction(state, PrepareRuntime);", initialize)
        self.assertIn("LuaInitResult::RuntimePreparationFailed", initialize)

    def test_lua_runtime_reports_preparation_failure_separately_from_state_creation(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        entry = (SOURCE / "runtime_entry.cpp").read_text()

        self.assertIn("RuntimePreparationFailed", header)
        initialize = runtime[runtime.index("LuaInitResult InitializeScript"):runtime.index("void DispatchPostUpdate()")]
        self.assertIn("LuaInitResult::RuntimePreparationFailed", initialize)
        self.assertIn("case LuaRuntime::LuaInitResult::RuntimePreparationFailed:", entry)
        self.assertIn("ReportStage7Failure(LuaRuntime::PreparationFailureDetail());", entry)

    def test_lua_runtime_reports_preparation_memory_failure_separately(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        entry = (SOURCE / "runtime_entry.cpp").read_text()

        self.assertIn("RuntimePreparationMemoryFailed", header)
        initialize = runtime[runtime.index("LuaInitResult InitializeScript"):runtime.index("void DispatchPostUpdate()")]
        self.assertIn("const int preparationStatus = lua_pcallk", initialize)
        self.assertIn("preparationStatus == LUA_ERRMEM", initialize)
        self.assertIn("LuaInitResult::RuntimePreparationMemoryFailed", initialize)
        self.assertIn("case LuaRuntime::LuaInitResult::RuntimePreparationMemoryFailed:", entry)
        self.assertIn("ReportStage7Failure(LuaRuntime::PreparationFailureDetail());", entry)

    def test_lua_runtime_exposes_the_failing_preparation_step_for_stage7(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()
        entry = (SOURCE / "runtime_entry.cpp").read_text()

        self.assertIn("u32 PreparationFailureDetail();", header)
        self.assertIn("g_PreparationStep", runtime)
        self.assertIn("g_PreparationStatus", runtime)
        self.assertIn("PreparationFailureDetail()", runtime)
        self.assertIn("LuaRuntime::PreparationFailureDetail()", entry)

    def test_lua_c_and_cpp_sources_share_the_c89_number_abi_configuration(self):
        rules = (ROOT / "misc" / "mk" / "common.mk").read_text()
        self.assertIn(
            "-DLUA_C89_NUMBERS",
            rules,
            "Lua C and C++ sources must share a numeric ABI that the devkitA64 C++ headers support",
        )

    def test_lua_diagnostic_magic_is_runtime_only_and_isolated_from_default_build(self):
        runtime = (SOURCE / "runtime_entry.cpp").read_text()
        hook = (SOURCE / "hook_manager.cpp").read_text()

        self.assertIn("0x49534141435F4C46ULL", runtime)
        self.assertIn("0x49534141435F4C46ULL", hook)
        self.assertIn("0x49534141435F4C50ULL", hook)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 7", runtime)

    def test_manager_update_dispatches_lua_only_after_the_original_game_function(self):
        hook = (SOURCE / "hook_manager.cpp").read_text()
        callback = hook[hook.index("static void Callback"):hook.index("};", hook.index("static void Callback"))]

        self.assertIn("Orig(self);", callback)
        self.assertIn("LuaRuntime::IsReady()", callback)
        self.assertIn("LuaRuntime::DispatchPostUpdate()", callback)
        self.assertLess(callback.index("Orig(self);"), callback.index("LuaRuntime::IsReady()"))
        self.assertLess(callback.index("LuaRuntime::IsReady()"), callback.index("LuaRuntime::DispatchPostUpdate()"))

    def test_worker_initializes_lua_after_the_relay_is_published_for_default_and_stage7(self):
        runtime = (SOURCE / "runtime_entry.cpp").read_text()
        worker = runtime[
            runtime.index("void ModuleWorker"):
            runtime.index('extern "C" void exl_main', runtime.index("void ModuleWorker"))
        ]

        self.assertIn("!defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 7", worker)
        self.assertIn("LuaRuntime::Initialize()", worker)
        self.assertLess(worker.index("HookInstallResult::Success"), worker.index("LuaRuntime::Initialize()"))

    def test_stage7_reports_callback_failure_or_120_post_updates(self):
        hook = (SOURCE / "hook_manager.cpp").read_text()

        for token in (
            "LuaRuntime::TakeCallbackError()",
            "(7ULL << 32) | 5ULL",
            "LuaRuntime::PostUpdateCount() >= 120",
            "(7ULL << 32) | 120ULL",
            "EXL_DIAGNOSTIC_STAGE == 7",
        ):
            with self.subTest(token=token):
                self.assertIn(token, hook)


if __name__ == "__main__":
    unittest.main()
