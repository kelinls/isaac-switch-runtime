"""多模组加载的**清单链**端到端验证（真实 JSON 后端 → 解析 → 路径 → 加载循环）。

## 这个用例覆盖什么（以及为什么不能只靠假后端）

`runtime/tests/integration/test_manifest_bridge.py` 验证的是"适配器把旧解析器的结果映射对"，
`test_mod_load_service.py` 验证的是"服务在假后端下行为正确"。多模组这一批新增的三种失败原因
（**一个都没启用** / **条数超过上限** / **字节坏了**）恰恰在**真实的**那段 JSON 解析里产生，
用假后端测不出来 —— 而真机上这三条的处置完全不同：

* `NoEnabledMod` ⇒ 清单合法但没东西可加载（用户忘了开模组）；
* `TooManyMods` ⇒ 清单合法但我们装不下（要改容量或删模组）；
* `InvalidJson/Mod` ⇒ 清单本身写坏了。

所以这里用**真实**的 `mod_manifest.cpp`（手写 JSON 解析器）+ 真实适配器 + 真实服务，只把
"读文件"和"跑 Lua"两个端口换成假的。

## 覆盖的判定

1. 两个 `enabled` + 一个 `enabled: false` ⇒ 正好两个，顺序 = 清单里的书写顺序；
2. 每个 Mod 的三条路径各自拼对（互不串台），`count` 与 `manifestBytes` 正确；
3. `LoadAll` 按顺序加载**两个** Mod（假 Lua 端口记下每一次的 entry/chunk/modRoot）；
4. 第一个 Mod 的 entry 缺失（纯资源）**不影响**第二个 Mod 的脚本执行；
5. 第二个 Mod 的脚本失败**不中断**这一批：第一个仍然 Executed，`failedIndex == 1`；
6. 5 个 `enabled` ⇒ `TooManyMods`（**不截断**），映射到 `CapacityExceeded`。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"
LEGACY = ROOT / "runtime" / "source"

DRIVER = textwrap.dedent(
    r"""
    #include "application/mod/manifest_parser.hpp"
    #include "application/mod/manifest_service.hpp"
    #include "application/mod/mod_load_service.hpp"
    #include "infrastructure/mod/manifest_selector_adapter.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>
    #include <string_view>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    ManifestParser Parser() {
        return ManifestParser{ManifestSelectorAdapter::SelectFunction(),
                              ManifestSelectorAdapter::SelectAllFunction()};
    }

    const char* kTwoEnabled =
        "{\"schema_version\":1,\"mods\":["
        "{\"directory\":\"ModA\",\"enabled\":true,\"entry\":\"mods/ModA/main.lua\"},"
        "{\"directory\":\"ModOff\",\"enabled\":false,\"entry\":\"mods/ModOff/main.lua\"},"
        "{\"directory\":\"ModB\",\"enabled\":true,\"entry\":\"mods/ModB/main.lua\"}]}";

    const char* kFiveEnabled =
        "{\"schema_version\":1,\"mods\":["
        "{\"directory\":\"M1\",\"enabled\":true},"
        "{\"directory\":\"M2\",\"enabled\":true},"
        "{\"directory\":\"M3\",\"enabled\":true},"
        "{\"directory\":\"M4\",\"enabled\":true},"
        "{\"directory\":\"M5\",\"enabled\":true}]}";

    const char* kNoneEnabled =
        "{\"schema_version\":1,\"mods\":[{\"directory\":\"ModA\",\"enabled\":false}]}";

    // 假的内容端口：清单返回**真实的那段 JSON 字节**（解析器是真的，喂垃圾进去只会得到
    // "清单坏了"，测不到任何东西）；入口脚本按"这个 Mod 的入口是否存在"决定成败。
    struct FakeContent final : IContentPort {
        const char* manifestText{nullptr};
        const char* missingEntryDirectory{nullptr};
        Status Size(std::string_view, std::size_t*) noexcept override { return Status::Ok(); }
        Status Read(std::string_view path, std::uint8_t* target, std::size_t capacity,
                    std::size_t* readCount) noexcept override {
            const std::string text(path);
            if (text.find("manifest.json") != std::string::npos) {
                const std::size_t bytes = std::strlen(manifestText);
                if (bytes > capacity) { return Status{StatusCode::CapacityExceeded}; }
                std::memcpy(target, manifestText, bytes);
                *readCount = bytes;
                return Status::Ok();
            }
            if (missingEntryDirectory != nullptr &&
                text.find(missingEntryDirectory) != std::string::npos) {
                return Status{StatusCode::NotFound};
            }
            const std::size_t bytes = 8;
            if (bytes > capacity) { return Status{StatusCode::CapacityExceeded}; }
            std::memset(target, 'x', bytes);
            *readCount = bytes;
            return Status::Ok();
        }
    };

    // 假的 Lua 端口：记下每一次加载的入参，并可以按"第几次"制造失败。
    struct FakeLua final : ILuaEnginePort {
        int loads{0};
        int failAtLoad{-1};                 // 第几次（0 起）加载返回失败；-1 = 都不失败
        char roots[8][128]{};
        char chunks[8][192]{};
        Status Initialize() noexcept override { return Status::Ok(); }
        bool IsReady() const noexcept override { return true; }
        Status RunChunk(const LuaChunk&) noexcept override {
            return Status{StatusCode::Unsupported};
        }
        Status LoadManifestMod(const ManifestModLoad& load,
                               std::uint32_t* failureDetail) noexcept override {
            const int index = loads;
            ++loads;
            if (index < 8) {
                if (load.modRoot != nullptr) {
                    std::strncpy(roots[index], load.modRoot, sizeof(roots[index]) - 1);
                }
                if (load.chunkName != nullptr) {
                    std::strncpy(chunks[index], load.chunkName, sizeof(chunks[index]) - 1);
                }
            }
            if (index == failAtLoad) {
                if (failureDetail != nullptr) { *failureDetail = 7; }
                return Status{StatusCode::Corrupted};
            }
            return Status::Ok();
        }
        Status CallGlobal(const char*, int, int*) noexcept override { return Status::Ok(); }
        void Shutdown() noexcept override {}
    };


    } // namespace

    int main() {
        // --- 1) 真实 JSON 后端：两个启用、一个关闭 -------------------------
        const std::size_t twoLength = std::strlen(kTwoEnabled);
        ManifestParseAllOutcome all = Parser().ParseAll(kTwoEnabled, twoLength);
        Check(all.failure == ManifestParseFailure::None, "two_enabled_parsed");
        Check(all.manifest.count == 2, "disabled_mod_skipped_and_order_kept");
        Check(std::strcmp(all.manifest.entries[0].directory.data(), "ModA") == 0,
              "first_mod_is_moda");
        Check(std::strcmp(all.manifest.entries[1].directory.data(), "ModB") == 0,
              "second_mod_is_modb");
        Check(all.manifest.entries[0].enabled, "first_mod_enabled_flag");

        // 单模组那条老口径不受影响：仍然是"第一个启用的"。
        ManifestParseOutcome first = Parser().Parse(kTwoEnabled, twoLength);
        Check(first.failure == ManifestParseFailure::None, "single_parse_still_works");
        Check(std::strcmp(first.manifest.directory.data(), "ModA") == 0,
              "single_parse_returns_first_enabled");

        // --- 2) 失败原因要逐条分开 -----------------------------------------
        ManifestParseAllOutcome none = Parser().ParseAll(kNoneEnabled, std::strlen(kNoneEnabled));
        Check(none.failure == ManifestParseFailure::NoEnabledMod, "no_enabled_mod_reported");
        ManifestParseAllOutcome tooMany =
            Parser().ParseAll(kFiveEnabled, std::strlen(kFiveEnabled));
        Check(tooMany.failure == ManifestParseFailure::TooManyMods,
              "too_many_mods_reported_not_truncated");
        ManifestParseAllOutcome broken = Parser().ParseAll("{", 1);
        Check(broken.failure != ManifestParseFailure::None, "broken_json_reported");

        // --- 3) 路径拼装：两个 Mod 各拼各的 -------------------------------
        FakeContent content{};
        content.manifestText = kTwoEnabled;
        FakeLua lua{};
        ManifestService manifestService{content, Parser()};
        ModLoadService loadService{content, lua};
        static std::uint8_t manifest[1024]{};
        ModLoadFailure failure{};
        static ResolvedManifestModBatch batch{};
        const Status resolved =
            manifestService.ResolveAll(manifest, sizeof(manifest), &batch, &failure);
        Check(resolved.ok(), "resolve_all_ok");
        Check(batch.count == 2, "batch_holds_two_mods");
        Check(batch.manifestBytes == std::strlen(kTwoEnabled), "manifest_bytes_reported");
        Check(std::strcmp(batch.mods[0].modRoot, "rom:/isaac_mods/mods/ModA") == 0,
              "first_mod_root");
        Check(std::strcmp(batch.mods[1].modRoot, "rom:/isaac_mods/mods/ModB") == 0,
              "second_mod_root");
        Check(std::strcmp(batch.mods[1].chunkName, "@rom:/isaac_mods/mods/ModB/main.lua") == 0,
              "second_mod_chunk_name");

        // --- 4) 加载循环：按顺序两个都跑 -----------------------------------
        static std::uint8_t entry[4096]{};
        ModLoadBatchOutcome outcome{};
        const Status loaded =
            loadService.LoadAll(batch, entry, sizeof(entry), &outcome, &failure);
        Check(loaded.ok(), "load_all_ok");
        Check(outcome.count == 2, "load_all_loaded_two");
        Check(!outcome.anyFailure, "load_all_no_failure");
        Check(lua.loads == 2, "lua_port_called_twice");
        Check(outcome.scripts[0].ScriptExecuted() && outcome.scripts[1].ScriptExecuted(),
              "both_scripts_executed");
        Check(std::strcmp(lua.roots[1], "rom:/isaac_mods/mods/ModB") == 0,
              "second_load_uses_its_own_root");

        // --- 5) 第一个 Mod 的入口缺失不影响第二个 ---------------------------
        {
            FakeContent sparse{};
            sparse.manifestText = kTwoEnabled;
            sparse.missingEntryDirectory = "ModA";
            FakeLua lua2{};
            ManifestService service{sparse, Parser()};
            ModLoadService loader{sparse, lua2};
            ModLoadBatchOutcome outcome2{};
            const Status status = loader.LoadAll(batch, entry, sizeof(entry), &outcome2, &failure);
            Check(status.ok(), "missing_entry_batch_ok");
            Check(outcome2.scripts[0].scriptState == ModScriptState::EntryAbsent,
                  "missing_entry_reported_for_first_mod");
            Check(outcome2.scripts[1].ScriptExecuted(), "second_mod_still_executed");
            Check(!outcome2.anyFailure, "missing_entry_is_not_a_batch_failure");
            Check(lua2.loads == 1, "only_the_scripted_mod_reaches_lua");
        }

        // --- 6) 第二个 Mod 的脚本失败不中断这一批 ---------------------------
        {
            FakeLua lua3{};
            lua3.failAtLoad = 1;
            ModLoadService loader{content, lua3};
            ModLoadBatchOutcome outcome3{};
            static_cast<void>(loader.LoadAll(batch, entry, sizeof(entry), &outcome3, &failure));
            Check(outcome3.anyFailure, "engine_failure_reported");
            Check(outcome3.failedIndex == 1, "failed_index_points_at_the_second_mod");
            Check(outcome3.scripts[0].ScriptExecuted(), "first_mod_survives");
            Check(failure.step == ModLoadStep::LuaInit, "failure_step_is_lua_init");
            Check(failure.detail == 7, "engine_detail_forwarded");
        }

        // --- 7) 超过上限：失败而不是截断 ------------------------------------
        //
        // 这一条走**真实的解析器**：清单里有 5 个启用的 Mod，我们的容量是 4。
        // 期望是 `TooManyMods`（不是"取前 4 个"）—— 截断会让第 5 个 Mod 安静地不加载，
        // 而症状是"某个 Mod 没生效"，查起来比一个明确的失败贵得多。
        {
            FakeContent big{};
            big.manifestText = kFiveEnabled;
            ManifestService service{big, Parser()};
            static std::uint8_t buffer[1024]{};
            static ResolvedManifestModBatch tooManyBatch{};
            ModLoadFailure tooManyFailure{};
            const Status status = service.ResolveAll(buffer, sizeof(buffer), &tooManyBatch,
                                                     &tooManyFailure);
            Check(!status.ok(), "too_many_mods_fails_the_resolve");
            Check(status.code() == StatusCode::CapacityExceeded,
                  "too_many_mods_maps_to_capacity_exceeded");
            Check(tooManyFailure.step == ModLoadStep::ManifestParse,
                  "too_many_mods_failure_step");
            Check(tooManyBatch.count == 0, "too_many_mods_yields_no_batch");
        }

        if (failures == 0) { std::printf("MULTI_MOD_MANIFEST_CHECKS_PASSED\n"); }
        return failures == 0 ? 0 : 1;
    }
    """
)


class MultiModManifestIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("需要宿主 C++ 编译器（本用例不需要 docker）")
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-multi-mod-manifest-")
        temporary = Path(cls.temporary.name)
        source = temporary / "multi_mod_manifest.cpp"
        source.write_text(DRIVER.lstrip(), encoding="utf-8")
        binary = temporary / "multi_mod_manifest"
        build = subprocess.run(
            [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
             "-I", str(SRC), "-I", str(LEGACY),
             str(source),
             str(SRC / "application" / "mod" / "manifest_parser.cpp"),
             str(SRC / "application" / "mod" / "manifest_service.cpp"),
             str(SRC / "application" / "mod" / "mod_load_service.cpp"),
             str(SRC / "infrastructure" / "mod" / "manifest_selector_adapter.cpp"),
             str(LEGACY / "mod_manifest.cpp"),
             "-o", str(binary)],
            capture_output=True, text=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)
        cls.binary = binary

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "temporary"):
            cls.temporary.cleanup()

    def test_multi_mod_manifest_chain(self):
        result = subprocess.run([str(self.binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("MULTI_MOD_MANIFEST_CHECKS_PASSED", result.stdout)
        self.assertNotIn("FAILED_CHECK", result.stdout)


if __name__ == "__main__":
    unittest.main()
