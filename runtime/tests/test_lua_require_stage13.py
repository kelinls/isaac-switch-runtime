import subprocess
import tempfile
import unittest
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class RequireFailure(Exception):
    def __init__(self, detail):
        super().__init__(detail)
        self.detail = detail


class RestrictedRequireMirror:
    def __init__(self, modules):
        self.modules = modules
        self.cache = {}
        self.read_count = {}
        self.depth = 0

    @staticmethod
    def module_path(module_name):
        if not isinstance(module_name, str) or not module_name:
            raise RequireFailure(19)
        if module_name[0] == "." or module_name[-1] == "." or ".." in module_name:
            raise RequireFailure(19)
        for character in module_name:
            if ord(character) > 0x7F or not (character.isalnum() or character in "_.-"):
                raise RequireFailure(19)
        return module_name.replace(".", "/") + ".lua"

    def require(self, module_name):
        path = self.module_path(module_name)
        if module_name in self.cache:
            return self.cache[module_name]
        if self.depth >= 8:
            raise RequireFailure(19)
        if path not in self.modules:
            raise RequireFailure(20)

        self.read_count[path] = self.read_count.get(path, 0) + 1
        source = self.modules[path]
        if source is None:
            raise RequireFailure(21)

        self.depth += 1
        try:
            result = source(self) if callable(source) else source
        except RequireFailure:
            raise
        except Exception as error:
            raise RequireFailure(22) from error
        finally:
            self.depth -= 1

        if result is None:
            result = True
        self.cache[module_name] = result
        return result


class LuaRequireStage13Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-require-stage13-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_runtime_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_observer.hpp"

#include <cstdio>
#include <cstring>

GameIsPausedObservation ObserveGameIsPaused(uintptr_t, uintptr_t) {
    return GameIsPausedObservation::OwnerUnreadable;
}

GameLevelStageObservation ReadCurrentGameLevelStage(uintptr_t, std::uint32_t*) {
    return GameLevelStageObservation::GameUnreadable;
}

GameIsGreedModeObservation ObserveGameIsGreedMode(uintptr_t, uintptr_t) {
    return GameIsGreedModeObservation::MethodUnavailable;
}
GameIsAscentObservation ObserveLevelIsAscent(uintptr_t, uintptr_t) { return GameIsAscentObservation::MethodUnavailable; }

GameItemPoolObservation ReadCurrentGameItemPool(uintptr_t, void**) {
    return GameItemPoolObservation::GameUnreadable;
}

GameRoomObservation ReadCurrentGameRoom(uintptr_t, void**) {
    return GameRoomObservation::RoomUnreadable;
}

GameRoomObservation ReadCurrentGameRoomType(uintptr_t, std::uint32_t*) {
    return GameRoomObservation::RoomUnreadable;
}

#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
namespace {
const char* g_Scenario = nullptr;
unsigned g_ModReads = 0;
unsigned g_MetadataReads = 0;

GameFileReader::TextReadResult CopySource(const char* source, u8* buffer,
                                          std::size_t capacity, std::size_t* length) {
    const std::size_t sourceLength = std::strlen(source);
    if (sourceLength == 0 || sourceLength > capacity) {
        return GameFileReader::TextReadResult::LengthOutOfRange;
    }
    std::memcpy(buffer, source, sourceLength);
    *length = sourceLength;
    return GameFileReader::TextReadResult::Success;
}
}

namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char* path, u8* buffer,
                            std::size_t capacity, std::size_t* length) {
    constexpr char root[] = "rom:/isaac_mods/mods/Probe/";
    if (std::strncmp(path, root, sizeof(root) - 1) != 0) {
        return TextReadResult::InvalidArgument;
    }
    const char* relative = path + sizeof(root) - 1;
    if (std::strcmp(g_Scenario, "depth") == 0 && relative[0] == 'm' &&
        relative[1] >= '0' && relative[1] <= '7' && std::strcmp(relative + 2, ".lua") == 0) {
        static char source[32];
        std::snprintf(source, sizeof(source), "return require('m%c')", relative[1] + 1);
        return CopySource(source, buffer, capacity, length);
    }
    if (std::strcmp(relative, "src/mod.lua") == 0) {
        ++g_ModReads;
        if (std::strcmp(g_Scenario, "missing") == 0) return TextReadResult::OpenFailed;
        if (std::strcmp(g_Scenario, "read") == 0) return TextReadResult::ReadMismatch;
        if (std::strcmp(g_Scenario, "compile") == 0) return CopySource("this is not lua", buffer, capacity, length);
        if (std::strcmp(g_Scenario, "execute") == 0) return CopySource("error('module failed')", buffer, capacity, length);
        if (std::strcmp(g_Scenario, "register_twice") == 0) {
            return CopySource("RegisterMod('First',1); RegisterMod('Second',1)", buffer, capacity, length);
        }
        if (std::strcmp(g_Scenario, "nested") == 0) return CopySource("return require('src.missing')", buffer, capacity, length);
        if (std::strcmp(g_Scenario, "nested_execute") == 0) return CopySource("return require('src.metadata')", buffer, capacity, length);
        return CopySource("local metadata=require('src.metadata'); return RegisterMod(metadata.name,1)",
                          buffer, capacity, length);
    }
    if (std::strcmp(relative, "src/plus+name.lua") == 0) {
        // 模块名里带 `+`（EID 的 `descriptions.ab+.en_us` / `descriptions.rep+.en_us` 就是这样）。
        ++g_ModReads;
        return CopySource("return 'plus-ok'", buffer, capacity, length);
    }
    if (std::strcmp(relative, "src/metadata.lua") == 0) {
        ++g_MetadataReads;
        if (std::strcmp(g_Scenario, "nested_execute") == 0) return CopySource("error('nested failure')", buffer, capacity, length);
        // `probe` 是给 `debug.getinfo` 用的：它的 `source` 必须是本模块的 chunk 路径
        // （`@rom:/isaac_mods/mods/Probe/src/metadata.lua`），EID 的
        // `EID:GetCurrentModPath()` 就是靠这个取真实 mod 路径的。
        return CopySource("return {name='Probe', probe=function() end}", buffer, capacity, length);
    }
    return TextReadResult::OpenFailed;
}
}
#endif

#if !defined(EXL_DIAGNOSTIC_STAGE)
namespace GameFileReader {
TextReadResult ReadTextFile(const Bindings&, const char*, u8*, std::size_t, std::size_t*) {
    return TextReadResult::InvalidArgument;
}
}
#endif

int main(int argc, char** argv) {
#if defined(EXL_DIAGNOSTIC_STAGE) && EXL_DIAGNOSTIC_STAGE == 13
    if (argc != 2) return 90;
    g_Scenario = argv[1];
    const char* entry = nullptr;
    if (std::strcmp(g_Scenario, "success") == 0) {
        // 批次 3 起 `debug` 存在，但**只**有只读的 `debug.getinfo`（EID 的
        // `GetCurrentModPath` 用它取真实 mod 路径）。能改写运行时状态的那些入口
        // （`sethook`/`setlocal`/`setupvalue`/`setmetatable`/`getregistry`）必须一个都没有；
        // `package`/`io`/`load`/`loadfile`/`dofile` 仍然为 nil。
        //
        // 2026-09-12 起 `os` **不再为 nil**（真机报告 `01789203211`：EID 的
        // `features/eid_tmtrainer.lua:199` 是 `if (debug and os.date("%m/%d") == "04/01")`，
        // 没有 `os` 就直接抛错、整包加载死在 200 行；PC 上两样都有）。策略因此变成
        // "给只读子集，但不给会写文件系统或结束进程的入口"。
        entry =
            "if package~=nil or io~=nil or load~=nil or loadfile~=nil or dofile~=nil then error('unsafe global') end "
            "if type(os)~='table' then error('os must exist') end "
            "if os.remove~=nil or os.rename~=nil or os.tmpname~=nil or os.exit~=nil then error('unsafe os member') end "
            "if debug==nil or type(debug.getinfo)~='function' then error('debug.getinfo must exist') end "
            "if debug.sethook~=nil or debug.setlocal~=nil or debug.setupvalue~=nil or debug.setmetatable~=nil or debug.setuservalue~=nil or debug.getregistry~=nil then error('unsafe debug member') end "
            "local metadata=require('src.metadata') "
            "local source=debug.getinfo(metadata.probe).source "
            "if source~='@rom:/isaac_mods/mods/Probe/src/metadata.lua' then error('debug.getinfo source: '..tostring(source)) end "
            "local mod=require('src.mod'); if require('src.mod')~=mod then error('cache mismatch') end "
            "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function(self) if self~=mod then error('self mismatch') end RuntimeTest.MarkPostUpdate() end)";
    } else if (std::strcmp(g_Scenario, "unsafe") == 0) {
        entry = "require('../escape')";
    } else if (std::strcmp(g_Scenario, "depth") == 0) {
        entry = "require('m0')";
    } else if (std::strcmp(g_Scenario, "wrong_callback") == 0) {
        entry = "local mod=RegisterMod('Wrong callback',1); mod:AddCallback(0,function() end)";
    } else if (std::strcmp(g_Scenario, "not_found_text") == 0) {
        // 批次 3：`require` 失败文本必须是 PC 的形状，而且**第二条** `no file '…lua'` 正好是
        // 本 Runtime 实际尝试的模块路径 —— 因为 EID 的 `EID:GetCurrentModPath()`
        // （`main.lua:163`）就是靠这两条反推自己的 mod 路径的。下面这段提取逻辑逐行照抄 EID。
        entry =
            R"lua(local ok, err = pcall(require, '')
if ok then error('require("") must fail without a reader for the empty name') end
local _, basePathStart = string.find(err, "no file '", 1)
if not basePathStart then error('the failure text lacks the PC shape: ' .. tostring(err)) end
local _, modPathStart = string.find(err, "no file '", basePathStart)
if not modPathStart then error('the failure text lists fewer than two candidates: ' .. tostring(err)) end
local modPathEnd, _ = string.find(err, ".lua'", modPathStart)
if not modPathEnd then error('the second candidate lacks the .lua suffix: ' .. tostring(err)) end
local modPath = string.sub(err, modPathStart+1, modPathEnd-1)
if modPath ~= 'rom:/isaac_mods/mods/Probe/' then
  error('EID would compute the wrong mod path: ' .. tostring(modPath) .. ' from ' .. tostring(err))
end
local mod = RegisterMod('Require text', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function() end))lua";
    } else if (std::strcmp(g_Scenario, "os_library") == 0) {
        // 真机报告 `01789203211`：EID 的 `features/eid_tmtrainer.lua:199` 是
        // `if (debug and os.date("%m/%d") == "04/01") then … end` —— `debug` 有、`os` 没有，
        // 于是 `os.date` 索引 nil 直接抛错、整包加载死在 200 行。这个场景把那行代码的形状
        // 与 `os` 的基本行为一起钉住。
        entry =
            "assert(type(os) == 'table', 'os library missing') "
            "assert(type(os.date) == 'function' and type(os.time) == 'function' "
            "and type(os.clock) == 'function' and type(os.difftime) == 'function', 'os members missing') "
            "local formatted = os.date('%m/%d') "
            "assert(type(formatted) == 'string' and #formatted == 5, 'os.date must format %m/%d') "
            "local now = os.time() "
            "assert(type(now) == 'number', 'os.time must return a number') "
            "local parts = os.date('*t', now) "
            "assert(type(parts) == 'table' and type(parts.year) == 'number' "
            "and type(parts.month) == 'number' and type(parts.day) == 'number', 'os.date(*t) shape') "
            "if debug and os.date('%m/%d') == '04/01' then end "
            "local mod = RegisterMod('Os', 1) "
            "mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function() end)";
    } else if (std::strcmp(g_Scenario, "plus_module_name") == 0) {
        // 2026-09-12 真机报告 `01789201969`：EID 的 `LoadLanguagePacks("ab+")` 会
        // `require("descriptions.ab+.en_us")`，而模块名白名单少了 `+`，于是被判成"不安全名字"、
        // 抛出 PC 形状的 not-found、被 EID 的 `pcall` 当"文件不存在"放过 —— 结果是
        // **ab+ 的 4 个语言包（`pt`/`bul`/`nl_nl`/`el_gr`，它们只存在于 `descriptions/ab+/`）
        // 永远没加载**，`EID.descriptions[lang]` 为 nil，崩在 `features/eid_mcm.lua:81`
        // （"attempt to index a nil value (field '?')"）。这个场景把"名字里带 `+` 的模块
        // 必须能 require"钉死在宿主机上。
        // 与其它期望 `Success` 的场景一样，脚本还必须登记 `MC_POST_UPDATE`
        // （否则 `InitializeStage13Mod` 返回 `MissingPostUpdateCallback`，与 require 无关）。
        entry =
            "require('src.plus+name') "
            "local mod = RegisterMod('Plus', 1) "
            "mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function() end)";
    } else {
        entry = "require('src.mod')";
    }

    GameFileReader::Bindings bindings{};
    const auto result = LuaRuntime::InitializeStage13Mod(
        entry, std::strlen(entry), "@main.lua", "rom:/isaac_mods/mods/Probe", bindings);
    if (std::strcmp(g_Scenario, "success") == 0) {
        if (result != LuaRuntime::LuaInitResult::Success || LuaRuntime::RequireFailureDetail() != 0 ||
            g_ModReads != 1 || g_MetadataReads != 1) {
            std::fprintf(stderr, "success: result=%u detail=%u modReads=%u metadataReads=%u\n",
                         static_cast<unsigned>(result), LuaRuntime::RequireFailureDetail(),
                         g_ModReads, g_MetadataReads);
            return 1;
        }
        LuaRuntime::DispatchPostUpdate();
        return LuaRuntime::PostUpdateCount() == 1 && !LuaRuntime::TakeCallbackError() ? 0 : 2;
    }
    if (std::strcmp(g_Scenario, "os_library") == 0) {
        // 脚本自身的 assert 已经覆盖语义；这里只确认初始化成功且没有 require 失败。
        return result == LuaRuntime::LuaInitResult::Success &&
                       LuaRuntime::RequireFailureDetail() == 0
                   ? 0
                   : 10;
    }
    if (std::strcmp(g_Scenario, "plus_module_name") == 0) {
        // 名字带 `+` 的模块必须**成功**：初始化成功、没有记录 require 失败、确实读了那一个文件。
        return result == LuaRuntime::LuaInitResult::Success &&
                       LuaRuntime::RequireFailureDetail() == 0 && g_ModReads == 1
                   ? 0
                   : 9;
    }
    if (std::strcmp(g_Scenario, "wrong_callback") == 0) {
        return result == LuaRuntime::LuaInitResult::ScriptRunFailed &&
                       LuaRuntime::RequireFailureDetail() == 0
                   ? 0
                   : 7;
    }
    if (std::strcmp(g_Scenario, "not_found_text") == 0) {
        // 脚本自己 pcall 了 `require("")`，所以初始化必须成功；但失败细节仍然被记下来
        // （`RaiseRequireFailure` 先记账再 `luaL_error`），而且**一次文件读取都不许发生**。
        return result == LuaRuntime::LuaInitResult::Success &&
                       LuaRuntime::RequireFailureDetail() == 19 && g_ModReads == 0
                   ? 0
                   : 8;
    }

    unsigned expected = 22;
    if (std::strcmp(g_Scenario, "unsafe") == 0 || std::strcmp(g_Scenario, "depth") == 0) expected = 19;
    if (std::strcmp(g_Scenario, "missing") == 0 || std::strcmp(g_Scenario, "nested") == 0) expected = 20;
    if (std::strcmp(g_Scenario, "read") == 0) expected = 21;
    if (std::strcmp(g_Scenario, "register_twice") == 0) expected = 26;
    if (std::strcmp(g_Scenario, "execute") == 0) expected = 27;
    if (std::strcmp(g_Scenario, "nested_execute") == 0) expected = 27;
    if (result != LuaRuntime::LuaInitResult::ScriptRunFailed ||
        LuaRuntime::RequireFailureDetail() != expected) return 3;
    if (std::strcmp(g_Scenario, "execute") == 0 &&
        LuaRuntime::RequireErrorTail() != 0x65206661696C6564ULL) return 5;
    if (std::strcmp(g_Scenario, "nested_execute") == 0 &&
        LuaRuntime::RequireErrorTail() != 0x206661696C757265ULL) return 6;
    if (std::strcmp(g_Scenario, "unsafe") == 0 && g_ModReads != 0) return 4;
    return 0;
#else
    (void)argc;
    (void)argv;
    const char script[] =
        // `MC_POST_UPDATE` 是 PC 契约里的 1（2026-09-12 从 0 修正），这条断言跟着改。
        "local mod=RegisterMod('Default',1); if ModCallbacks.MC_POST_UPDATE~=1 then error('callback value') end "
        "mod:AddCallback(ModCallbacks.MC_POST_UPDATE,function(self,...) if self~=mod or select('#',...)~=0 then error('unexpected callback contract') end RuntimeTest.MarkPostUpdate() end)";
    if (LuaRuntime::InitializeFromBuffer(script, sizeof(script) - 1, "@default.lua") !=
        LuaRuntime::LuaInitResult::Success) return 5;
    LuaRuntime::DispatchPostUpdate();
    return LuaRuntime::PostUpdateCount() == 1 && !LuaRuntime::TakeCallbackError() ? 0 : 6;
#endif
}
'''.lstrip()
        )

        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            lua_object = temporary / (lua_source.stem + ".o")
            compile_lua = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root),
                 "-c", str(lua_source), "-o", str(lua_object)],
                text=True,
                capture_output=True,
            )
            if compile_lua.returncode != 0:
                raise AssertionError(compile_lua.stdout + compile_lua.stderr)
            lua_objects.append(lua_object)

        common = [
            "c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
            "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
            "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
            "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
            "-I", str(lua_root), str(harness),
            *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
            *(str(path) for path in lua_objects), "-lm",
        ]
        cls.stage13_harness = temporary / "stage13_harness"
        stage13_build = subprocess.run(
            [*common[:1], "-DEXL_DIAGNOSTIC_STAGE=13", *common[1:], "-o", str(cls.stage13_harness)],
            text=True,
            capture_output=True,
        )
        if stage13_build.returncode != 0:
            raise AssertionError(stage13_build.stdout + stage13_build.stderr)

        cls.legacy_harnesses = []
        for stage in (None, 7, 12):
            executable = temporary / ("default_harness" if stage is None else f"stage{stage}_harness")
            command = list(common)
            if stage is not None:
                command.insert(1, f"-DEXL_DIAGNOSTIC_STAGE={stage}")
            build = subprocess.run([*command, "-o", str(executable)], text=True, capture_output=True)
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            cls.legacy_harnesses.append(executable)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_harness(self, executable, *arguments):
        result = subprocess.run([str(executable), *arguments], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_production_loader_executes_cache_and_pc_callback_contract(self):
        self.run_harness(self.stage13_harness, "success")

    def test_the_os_library_covers_what_mods_use_at_load_time(self):
        """`os.date` / `os.time` / `os.clock` 必须存在且形状正确。

        真机报告 `01789203211`：EID 的 `features/eid_tmtrainer.lua:199` 那行
        `if (debug and os.date("%m/%d") == "04/01") then` 因为 `os` 不存在而抛错，
        整包加载死在 200 行（PC 上两样都在，所以 PC 不会出错）。
        """
        self.run_harness(self.stage13_harness, "os_library")

    def test_a_module_name_may_contain_a_plus_sign(self):
        """`require("descriptions.ab+.en_us")` 必须成功（EID 的 ab+ 语言包靠它加载）。

        真机报告 `01789201969` 的根因：模块名白名单少了 `+` ⇒ ab+ 语言包全没加载 ⇒
        `EID.descriptions[lang]` 为 nil ⇒ `features/eid_mcm.lua:81` 崩。
        """
        self.run_harness(self.stage13_harness, "plus_module_name")

    def test_production_loader_reports_each_failure_boundary(self):
        for scenario in ("unsafe", "missing", "read", "compile", "execute", "nested_execute", "register_twice", "nested", "depth", "wrong_callback"):
            with self.subTest(scenario=scenario):
                self.run_harness(self.stage13_harness, scenario)

    def test_production_default_stage7_and_stage12_keep_pc_mod_first_callback_contract(self):
        for executable in self.legacy_harnesses:
            with self.subTest(executable=executable.name):
                self.run_harness(executable)

    def test_require_failure_text_matches_the_pc_shape_eid_parses(self):
        """`require` 的失败文本必须能被 EID 的 `GetCurrentModPath()` 解析出 mod 目录。

        EID（`main.lua:163`）在**没有 debug 库**时用 `pcall(require, "")` 的错误文本反推自己的
        路径：第一次 `string.find(err, "no file '")` 拿到第一条的结尾，第二次 find 从那里找
        **第二条**，然后取"第二个单引号之后、到下一个 `.lua'` 之前"的文本。所以失败文本至少要有
        两条 `no file '…lua'`，而且**第二条**必须是本 Runtime 真正尝试的模块路径
        （空名 → `<mod 根>/.lua`，EID 于是得到 `<mod 根>/`）。

        这些断言全在 entry 脚本里（照抄 EID 的提取逻辑），harness 只核对"初始化成功 +
        失败细节 19 + 一次文件读取都没发生"。
        """
        self.run_harness(self.stage13_harness, "not_found_text")

    def test_dot_module_name_maps_to_relative_lua_path(self):
        self.assertEqual(RestrictedRequireMirror.module_path("src.metadata"), "src/metadata.lua")

    def test_successful_module_result_is_cached_once(self):
        expected = {"name": "Runtime Require Probe"}
        loader = RestrictedRequireMirror({"src/metadata.lua": expected})

        first = loader.require("src.metadata")
        second = loader.require("src.metadata")

        self.assertIs(first, expected)
        self.assertIs(second, expected)
        self.assertEqual(loader.read_count, {"src/metadata.lua": 1})

    def test_rejects_unsafe_or_non_ascii_module_names_before_read(self):
        loader = RestrictedRequireMirror({})
        for name in ("../escape", "src/escape", "src\\escape", ".src", "src.", "src..escape", "src.\u6d4b\u8bd5"):
            with self.subTest(name=name), self.assertRaises(RequireFailure) as raised:
                loader.require(name)
            self.assertEqual(raised.exception.detail, 19)
        self.assertEqual(loader.read_count, {})

    def test_nested_open_failure_preserves_specific_detail(self):
        loader = RestrictedRequireMirror({"src/mod.lua": lambda runtime: runtime.require("src.missing")})
        with self.assertRaises(RequireFailure) as raised:
            loader.require("src.mod")
        self.assertEqual(raised.exception.detail, 20)

    def test_read_and_execution_failures_have_distinct_details(self):
        unreadable = RestrictedRequireMirror({"src/unreadable.lua": None})
        with self.assertRaises(RequireFailure) as raised:
            unreadable.require("src.unreadable")
        self.assertEqual(raised.exception.detail, 21)

        def broken(_runtime):
            raise RuntimeError("compile or execute")

        invalid = RestrictedRequireMirror({"src/broken.lua": broken})
        with self.assertRaises(RequireFailure) as raised:
            invalid.require("src.broken")
        self.assertEqual(raised.exception.detail, 22)

    def test_nested_load_depth_is_capped_at_eight(self):
        modules = {}
        for index in range(9):
            name = f"m{index}"
            next_name = f"m{index + 1}"
            modules[f"{name}.lua"] = lambda runtime, child=next_name: runtime.require(child)
        loader = RestrictedRequireMirror(modules)

        with self.assertRaises(RequireFailure) as raised:
            loader.require("m0")
        self.assertEqual(raised.exception.detail, 19)
        self.assertEqual(sum(loader.read_count.values()), 8)

    def test_source_is_stage13_only_bounded_and_uses_task2_reader(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        runtime = (SOURCE / "lua_runtime.cpp").read_text()

        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", header)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 13", runtime)
        self.assertIn("GameFileReader::ReadTextFile", runtime)
        self.assertIn("kRomfsModScriptMaximumLength", runtime)
        self.assertIn('kStage13ModRootPrefix[] = "rom:/isaac_mods/mods/"', runtime)
        self.assertIn("std::atomic<u32> g_RequireFailureDetail", runtime)
        self.assertIn("g_RequireCacheRef", runtime)
        self.assertIn("u64 RequireErrorTail();", header)
        self.assertIn("luaL_ref(state, LUA_REGISTRYINDEX)", runtime)
        for name in ("load", "loadfile", "dofile"):
            with self.subTest(removed_global=name):
                self.assertIn(f'lua_setglobal(state, "{name}")', runtime)
        for forbidden in ("luaopen_package", "luaopen_io", "luaopen_os", "luaopen_debug", "luaL_loadfile"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, runtime)

    def test_stage13_api_is_hidden_from_default_stage7_and_stage12_headers(self):
        header = (SOURCE / "lua_runtime.hpp").read_text()
        declaration = header.index("LuaInitResult InitializeStage13Mod")
        gate_start = header.rfind("#if defined(EXL_DIAGNOSTIC_STAGE)", 0, declaration)
        gate_end = header.index("#endif", gate_start) + len("#endif")
        translation_unit = header[gate_start:gate_end] + "\n"

        with tempfile.TemporaryDirectory(prefix="isaac-stage13-lua-header-") as temporary:
            source = Path(temporary) / "stage13_api.hpp"
            source.write_text(translation_unit)
            for stage in (None, 7, 12, 13):
                command = ["c++", "-E", "-P", "-x", "c++", str(source)]
                if stage is not None:
                    command.insert(1, f"-DEXL_DIAGNOSTIC_STAGE={stage}")
                result = subprocess.run(command, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if stage == 13:
                    self.assertIn("InitializeStage13Mod", result.stdout)
                    self.assertIn("RequireFailureDetail", result.stdout)
                else:
                    self.assertNotIn("InitializeStage13Mod", result.stdout)
                    self.assertNotIn("RequireFailureDetail", result.stdout)


if __name__ == "__main__":
    unittest.main()
