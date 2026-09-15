"""纯资源 Mod（清单无 `entry`）在加载路径上的契约。

PC 上"只有 `resources/`、没有 `main.lua`"的 Mod 是合法形态：游戏把它的 `resources/` 挂上就完事。
本次改动让加载器对两种输入都不再判为失败 —— 清单**没写** `entry`，或 `entry` 指向的文件包内
**不存在** —— 而是注册内容挂载点、跳过 Lua 初始化，并把"资源型（已挂载、无脚本）"作为结果报出去。

这个测试把**真实源码**编成宿主可执行文件（清单解析 + 选择适配器 + 清单服务 + 加载用例），
用假的内容端口与假的 Lua 端口驱动它，所以它验证的是行为，不是文本：

  * 无 entry  -> Resolve 成功、Load 成功、Lua 端口一次都没被调用；
  * entry 文件不存在 -> 同上；
  * 其它读失败、空 entry 文件、Lua 失败、清单损坏 -> 仍然失败（回归，证明判定没有被放宽）。

另外核对：打包工具产出的"无脚本 Mod"清单能被这套解析器选中，并且两个清单缓冲区常量一致。
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"
SRC = ROOT / "runtime" / "src"

HARNESS = r"""
#include "application/mod/manifest_parser.hpp"
#include "application/mod/manifest_service.hpp"
#include "application/mod/mod_load_service.hpp"
#include "infrastructure/mod/manifest_selector_adapter.hpp"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

using namespace isaac::runtime;

namespace {

const char* StateName(ModScriptState state) {
    switch (state) {
        case ModScriptState::Executed: return "Executed";
        case ModScriptState::DeclaredScriptless: return "DeclaredScriptless";
        case ModScriptState::EntryAbsent: return "EntryAbsent";
    }
    return "?";
}

const char* StepName(ModLoadStep step) {
    switch (step) {
        case ModLoadStep::None: return "None";
        case ModLoadStep::ManifestRead: return "ManifestRead";
        case ModLoadStep::ManifestParse: return "ManifestParse";
        case ModLoadStep::PathBuild: return "PathBuild";
        case ModLoadStep::EntryRead: return "EntryRead";
        case ModLoadStep::LuaInit: return "LuaInit";
        case ModLoadStep::Request: return "Request";
    }
    return "?";
}

struct FakeContent : IContentPort {
    std::map<std::string, std::string> files;
    std::map<std::string, StatusCode> forced;

    Status Size(std::string_view, std::size_t*) noexcept override {
        return Status{StatusCode::Unsupported};
    }

    Status Read(std::string_view path, std::uint8_t* target, std::size_t capacity,
                std::size_t* readCount) noexcept override {
        const std::string key(path);
        const auto forcedEntry = forced.find(key);
        if (forcedEntry != forced.end()) {
            return Status{forcedEntry->second};
        }
        const auto entry = files.find(key);
        if (entry == files.end()) {
            return Status{StatusCode::NotFound};
        }
        if (entry->second.size() > capacity) {
            return Status{StatusCode::CapacityExceeded};
        }
        std::memcpy(target, entry->second.data(), entry->second.size());
        *readCount = entry->second.size();
        return Status::Ok();
    }
};

struct FakeLua : ILuaEnginePort {
    int loadCalls{0};
    Status nextResult{StatusCode::Ok};
    std::uint32_t nextDetail{0};
    std::size_t lastEntryLength{0};
    std::string lastChunkName;
    std::string lastModRoot;

    Status Initialize() noexcept override { return Status::Ok(); }
    bool IsReady() const noexcept override { return loadCalls > 0; }
    Status RunChunk(const LuaChunk&) noexcept override { return Status::Ok(); }
    Status LoadManifestMod(const ManifestModLoad& load, std::uint32_t* detail) noexcept override {
        ++loadCalls;
        lastEntryLength = load.entryLength;
        lastChunkName = load.chunkName != nullptr ? load.chunkName : "";
        lastModRoot = load.modRoot != nullptr ? load.modRoot : "";
        if (detail != nullptr) {
            *detail = nextDetail;
        }
        return nextResult;
    }
    Status CallGlobal(const char*, int, int*) noexcept override { return Status::Ok(); }
    void Shutdown() noexcept override {}
};

struct Report {
    const char* resolve{"ok"};
    bool hasEntry{false};
    std::string entryPath;
    std::string modRoot;
    std::string chunkName;
    const char* load{"ok"};
    const char* state{"Executed"};
    std::size_t entryBytes{0};
    std::size_t manifestBytes{0};
    int luaCalls{0};
    const char* step{"None"};
    std::uint32_t detail{0};
};

Report Run(const std::string& manifest, FakeContent& content, FakeLua& lua,
           std::size_t entryCapacity, bool withFile) {
    content.files["rom:/isaac_mods/manifest.json"] = manifest;
    ManifestService manifestService{content, ManifestParser{ManifestSelectorAdapter::SelectFunction()}};
    ModLoadService loadService{content, lua};

    std::vector<std::uint8_t> manifestBuffer(kRomfsModManifestMaximumLength);
    std::vector<char> entryPath(kModEntryPathCapacity);
    std::vector<char> modRoot(kModRootPathCapacity);
    std::vector<char> chunkName(kModChunkNameCapacity);
    std::vector<std::uint8_t> entryBuffer(entryCapacity);

    ModLoadFailure failure{};
    Report report{};
    const Result<ResolvedManifestMod> resolved = manifestService.Resolve(
        manifestBuffer.data(), manifestBuffer.size(), entryPath.data(), entryPath.size(),
        modRoot.data(), modRoot.size(), chunkName.data(), chunkName.size(), &failure);
    if (!resolved.ok()) {
        report.resolve = "failed";
        report.load = "skipped";
        report.step = StepName(failure.step);
        report.detail = failure.detail;
        return report;
    }
    report.hasEntry = resolved.value().hasEntry;
    report.manifestBytes = resolved.value().manifestBytes;
    report.entryPath = resolved.value().entryPath;
    report.modRoot = resolved.value().modRoot;
    report.chunkName = resolved.value().chunkName;
    if (!withFile) {
        return report;
    }

    ModLoadRequest request{};
    request.resolved = &resolved.value();
    request.entryBuffer = entryBuffer.data();
    request.entryCapacity = entryBuffer.size();
    const Result<ModLoadOutcome> loaded = loadService.Load(request, &failure);
    report.load = loaded.ok() ? "ok" : "failed";
    report.luaCalls = lua.loadCalls;
    report.step = StepName(failure.step);
    report.detail = failure.detail;
    if (loaded.ok()) {
        report.state = StateName(loaded.value().scriptState);
        report.entryBytes = loaded.value().entryBytes;
    }
    return report;
}

void Print(const char* name, const Report& report) {
    std::printf(
        "CASE %s resolve=%s hasEntry=%d entryPath=%s modRoot=%s chunkName=%s load=%s state=%s "
        "entryBytes=%zu manifestBytes=%zu lua=%d step=%s detail=%u\n",
        name, report.resolve, report.hasEntry ? 1 : 0, report.entryPath.c_str(),
        report.modRoot.c_str(), report.chunkName.c_str(), report.load, report.state,
        report.entryBytes, report.manifestBytes, report.luaCalls, report.step, report.detail);
}

std::string ReadFile(const char* path) {
    std::ifstream input(path, std::ios::binary);
    std::ostringstream buffer;
    buffer << input.rdbuf();
    return buffer.str();
}

std::string ScriptedManifest(const char* directory) {
    std::string json = R"({"schema_version":1,"mods":[{"directory":")";
    json += directory;
    json += R"(","entry":"mods/)";
    json += directory;
    json += R"(/main.lua","enabled":true}]})";
    return json;
}

std::string ScriptlessManifest(const char* directory) {
    std::string json = R"({"schema_version":1,"mods":[{"directory":")";
    json += directory;
    json += R"(","content_only":true,"enabled":true,"files":[{"path":"mods/)";
    json += directory;
    json += R"(/resources/gfx/x.pcx","sha256":"00"}]}]})";
    return json;
}

}  // namespace

int main(int argc, char** argv) {
    // SELECT 模式：把给定清单文件喂给同一套解析器（打包工具 -> 解析器的端到端检查）。
    if (argc > 1) {
        FakeContent content;
        FakeLua lua;
        const Report report = Run(ReadFile(argv[1]), content, lua, 4096, false);
        std::printf("SELECT resolve=%s hasEntry=%d entryPath=%s modRoot=%s chunkName=%s\n",
                    report.resolve, report.hasEntry ? 1 : 0, report.entryPath.c_str(),
                    report.modRoot.c_str(), report.chunkName.c_str());
        return 0;
    }

    constexpr const char* kEntryText = "return RegisterMod('Alpha', 1)";

    {
        FakeContent content;
        FakeLua lua;
        content.files["rom:/isaac_mods/mods/Alpha/main.lua"] = kEntryText;
        Print("scripted", Run(ScriptedManifest("Alpha"), content, lua, 4096, true));
    }
    {
        FakeContent content;
        FakeLua lua;
        Print("scriptless_declared", Run(ScriptlessManifest("Textures"), content, lua, 4096, true));
    }
    {
        // 清单写了 entry，但包里没有这个文件：以前整包加载失败，现在只挂载内容。
        FakeContent content;
        FakeLua lua;
        Print("scriptless_missing_file", Run(ScriptedManifest("Textures"), content, lua, 4096, true));
    }
    {
        // 没有 entry 时连脚本缓冲区都不需要（缓冲区指针为空也要成功）。
        FakeContent content;
        FakeLua lua;
        content.files["rom:/isaac_mods/mods/Textures/main.lua"] = kEntryText;
        Print("scriptless_without_buffer", Run(ScriptlessManifest("Textures"), content, lua, 1, true));
    }
    {
        // 回归：非 NotFound 的读失败仍然是失败。
        FakeContent content;
        FakeLua lua;
        content.forced["rom:/isaac_mods/mods/Alpha/main.lua"] = StatusCode::IoFailure;
        Print("entry_io_failure", Run(ScriptedManifest("Alpha"), content, lua, 4096, true));
    }
    {
        // 回归：entry 存在但长度为 0 仍是失败。
        FakeContent content;
        FakeLua lua;
        content.files["rom:/isaac_mods/mods/Alpha/main.lua"] = "";
        Print("entry_empty_file", Run(ScriptedManifest("Alpha"), content, lua, 4096, true));
    }
    {
        // 回归：脚本缓冲区装不下 entry 仍是失败（容量错误不是"文件不存在"）。
        FakeContent content;
        FakeLua lua;
        content.files["rom:/isaac_mods/mods/Alpha/main.lua"] = kEntryText;
        Print("entry_capacity", Run(ScriptedManifest("Alpha"), content, lua, 4, true));
    }
    {
        // 回归：Lua 初始化失败仍然把引擎的 detail 报出来。
        FakeContent content;
        FakeLua lua;
        lua.nextResult = Status{StatusCode::Rejected};
        lua.nextDetail = 7;
        content.files["rom:/isaac_mods/mods/Alpha/main.lua"] = kEntryText;
        Print("lua_failure", Run(ScriptedManifest("Alpha"), content, lua, 4096, true));
    }
    {
        // 回归：清单本身损坏仍然是 ManifestParse 失败。
        FakeContent content;
        FakeLua lua;
        Print("malformed_manifest", Run("{not json", content, lua, 4096, true));
    }
    {
        // 回归：没有 enabled 的 Mod 仍然是 NotFound。
        FakeContent content;
        FakeLua lua;
        Print("no_enabled_mod", Run(
                  R"({"schema_version":1,"mods":[{"directory":"Alpha","entry":"mods/Alpha/main.lua","enabled":false}]})",
                  content, lua, 4096, true));
    }
    {
        // 回归：请求不可用仍然是 InvalidArgument（不读清单）。
        FakeContent content;
        FakeLua lua;
        ModLoadService service{content, lua};
        ModLoadFailure failure{};
        std::vector<std::uint8_t> buffer(64);
        ModLoadRequest request{};
        request.entryBuffer = buffer.data();
        request.entryCapacity = buffer.size();
        const Result<ModLoadOutcome> loaded = service.Load(request, &failure);
        std::printf("CASE invalid_request resolve=skipped hasEntry=0 entryPath= modRoot= chunkName= "
                    "load=%s state=%s entryBytes=0 manifestBytes=0 lua=%d step=%s detail=%u\n",
                    loaded.ok() ? "ok" : "failed", "Executed", lua.loadCalls, StepName(failure.step),
                    failure.detail);
    }
    return 0;
}
"""


def _parse_cases(output: str) -> dict[str, dict[str, str]]:
    cases: dict[str, dict[str, str]] = {}
    for line in output.splitlines():
        if not line.startswith("CASE "):
            continue
        fields = line.split()
        name = fields[1]
        cases[name] = dict(field.split("=", 1) for field in fields[2:])
    return cases


class ScriptlessModLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-scriptless-mod-")
        temporary = Path(cls.temporary.name)
        harness = temporary / "scriptless_harness.cpp"
        harness.write_text(HARNESS.lstrip(), encoding="utf-8")
        cls.executable = temporary / "scriptless_harness"
        build = subprocess.run(
            [
                "c++", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                "-DEXL_DIAGNOSTIC_STAGE=13",
                "-I", str(SOURCE), "-I", str(SRC),
                str(harness),
                str(SRC / "application/mod/manifest_parser.cpp"),
                str(SRC / "application/mod/manifest_service.cpp"),
                str(SRC / "application/mod/mod_load_service.cpp"),
                str(SRC / "infrastructure/mod/manifest_selector_adapter.cpp"),
                str(SOURCE / "mod_manifest.cpp"),
                "-o", str(cls.executable),
            ],
            text=True,
            capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)
        cls.executed = subprocess.run([str(cls.executable)], text=True, capture_output=True)
        if cls.executed.returncode != 0:
            raise AssertionError(cls.executed.stdout + cls.executed.stderr)
        cls.cases = _parse_cases(cls.executed.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def case(self, name):
        self.assertIn(name, self.cases, self.executed.stdout)
        return self.cases[name]

    def test_manifest_without_entry_loads_and_skips_lua(self):
        case = self.case("scriptless_declared")
        self.assertEqual(case["resolve"], "ok")
        self.assertEqual(case["hasEntry"], "0")
        self.assertEqual(case["entryPath"], "")
        self.assertEqual(case["chunkName"], "")
        self.assertEqual(case["modRoot"], "rom:/isaac_mods/mods/Textures")
        self.assertEqual(case["load"], "ok")
        self.assertEqual(case["state"], "DeclaredScriptless")
        self.assertEqual(case["entryBytes"], "0")
        self.assertEqual(case["lua"], "0", "资源型 Mod 不得初始化 Lua")
        self.assertEqual(case["step"], "None", "不是失败，failure 结构必须保持空")

    def test_missing_entry_file_loads_and_skips_lua(self):
        case = self.case("scriptless_missing_file")
        self.assertEqual(case["resolve"], "ok")
        self.assertEqual(case["hasEntry"], "1", "清单确实写了 entry，只是文件不在包里")
        self.assertEqual(case["entryPath"], "rom:/isaac_mods/mods/Textures/main.lua")
        self.assertEqual(case["load"], "ok")
        self.assertEqual(case["state"], "EntryAbsent")
        self.assertEqual(case["lua"], "0")
        self.assertEqual(case["step"], "None")

    def test_scriptless_load_needs_no_entry_buffer(self):
        case = self.case("scriptless_without_buffer")
        self.assertEqual(case["load"], "ok")
        self.assertEqual(case["state"], "DeclaredScriptless")
        self.assertEqual(case["lua"], "0")

    def test_scripted_mod_behavior_is_unchanged(self):
        case = self.case("scripted")
        self.assertEqual(case["resolve"], "ok")
        self.assertEqual(case["hasEntry"], "1")
        self.assertEqual(case["entryPath"], "rom:/isaac_mods/mods/Alpha/main.lua")
        self.assertEqual(case["chunkName"], "@rom:/isaac_mods/mods/Alpha/main.lua")
        self.assertEqual(case["modRoot"], "rom:/isaac_mods/mods/Alpha")
        self.assertEqual(case["load"], "ok")
        self.assertEqual(case["state"], "Executed")
        self.assertEqual(case["entryBytes"], str(len("return RegisterMod('Alpha', 1)")))
        self.assertEqual(case["lua"], "1", "有脚本的 Mod 必须照旧初始化 Lua")
        self.assertEqual(case["step"], "None")
        self.assertEqual(case["detail"], "0")

    def test_other_failures_are_not_downgraded(self):
        # 只有"文件不存在"降级为资源型加载；容量错误、I/O 错误、空文件都还是失败，
        # 且 detail 就是 StatusCode 的数值（IoFailure=6、Corrupted=7、CapacityExceeded=5）。
        for name, detail in (
            ("entry_io_failure", "6"),
            ("entry_empty_file", "7"),
            ("entry_capacity", "5"),
        ):
            case = self.case(name)
            with self.subTest(name=name):
                self.assertEqual(case["load"], "failed")
                self.assertEqual(case["step"], "EntryRead")
                self.assertEqual(case["detail"], detail)
                self.assertEqual(case["lua"], "0")

    def test_lua_failure_keeps_engine_detail(self):
        case = self.case("lua_failure")
        self.assertEqual(case["load"], "failed")
        self.assertEqual(case["step"], "LuaInit")
        self.assertEqual(case["detail"], "7")
        self.assertEqual(case["lua"], "1")

    def test_manifest_errors_are_unchanged(self):
        malformed = self.case("malformed_manifest")
        self.assertEqual(malformed["resolve"], "failed")
        self.assertEqual(malformed["load"], "skipped")
        self.assertEqual(malformed["step"], "ManifestParse")
        disabled = self.case("no_enabled_mod")
        self.assertEqual(disabled["resolve"], "failed")
        self.assertEqual(disabled["step"], "ManifestParse")
        # `ManifestSelectFunction` 只有成功/失败两种结果，选择适配器分不出"没有 enabled"
        # 与"JSON 坏了"，所以 `ManifestParser` 统一报 InvalidJson，服务映射为 Corrupted(7)。
        self.assertEqual(disabled["detail"], "7")

    def test_invalid_request_still_rejected(self):
        case = self.case("invalid_request")
        self.assertEqual(case["load"], "failed")
        self.assertEqual(case["step"], "Request")
        self.assertEqual(case["detail"], "1")  # InvalidArgument
        self.assertEqual(case["lua"], "0")


    def test_packer_writes_no_entry_for_a_resource_only_mod(self):
        with tempfile.TemporaryDirectory(prefix="isaac-scriptless-pack-") as temporary:
            workspace = Path(temporary)
            mods_root = workspace / "mods"
            mod = mods_root / "qualityonsprites"
            (mod / "resources/gfx/items/collectibles").mkdir(parents=True)
            (mod / "metadata.xml").write_text(
                "<metadata><name>QualityOnSprite</name><id>1</id></metadata>", encoding="utf-8"
            )
            (mod / "resources/gfx/items/collectibles/x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            output = workspace / "romfs"
            generated = subprocess.run(
                ["python3", "-m", "tools.inspect_pc_mod", "--mods-root", str(mods_root),
                 "--romfs-output", str(output)],
                cwd=ROOT, text=True, capture_output=True,
            )
            self.assertEqual(generated.returncode, 0, generated.stdout + generated.stderr)
            manifest_path = output / "atmosphere/contents/010021C000B6A000/romfs/isaac_mods/manifest.json"
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            packed = payload["mods"][0]
            self.assertNotIn("entry", packed, "无脚本 Mod 的清单里不得出现 entry 字段")
            self.assertTrue(packed["content_only"])
            self.assertTrue(packed["enabled"])
            self.assertEqual(packed["directory"], "qualityonsprites")
            # 端到端：打包工具产出的清单必须能被加载器那套解析器选中。
            selected = subprocess.run(
                [str(self.executable), str(manifest_path)], text=True, capture_output=True,
            )
            self.assertEqual(selected.returncode, 0, selected.stdout + selected.stderr)
            self.assertIn("SELECT resolve=ok hasEntry=0", selected.stdout)
            self.assertIn("modRoot=rom:/isaac_mods/mods/qualityonsprites", selected.stdout)


class ManifestCapacityContractTests(unittest.TestCase):
    def test_both_manifest_capacity_definitions_agree(self):
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        service = (SRC / "application/mod/manifest_service.hpp").read_text(encoding="utf-8")
        pattern = re.compile(r"kRomfsModManifestMaximumLength\s*=\s*(\d+)")
        legacy = pattern.search(constants)
        layered = pattern.search(service)
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(layered)
        self.assertEqual(legacy.group(1), layered.group(1))
        # 2026-09-14：真实贴图 Mod 的清单是 309 KB，131072 会让它直接 ManifestRead 失败。
        self.assertEqual(layered.group(1), "524288")

    def test_both_script_capacity_definitions_agree(self):
        """脚本缓冲区上限（入口脚本与 `require` 共用）在两处定义必须一致且足够大。

        2026-09-12 真机报告 `01789200504` 的根因就是这里：16 KiB 的脚本缓冲区装不下 EID 的
        `main.lua`（87,328 字节），`ReadTextFile` 返回 `LengthOutOfRange` → `EntryRead` 失败 →
        **EID 的 Lua 一行都没跑**，现象却是"加载不报错、回调注册表为空、屏幕上什么都没有"。
        这条断言的作用是让"缓冲区缩回去"这件事在宿主机上就失败，而不是再花一轮真机。
        """
        constants = (SOURCE / "runtime_constants.hpp").read_text(encoding="utf-8")
        service = (SRC / "application/mod/manifest_service.hpp").read_text(encoding="utf-8")
        pattern = re.compile(r"kRomfsModScriptMaximumLength\s*=\s*(\d+)")
        legacy = pattern.search(constants)
        layered = pattern.search(service)
        self.assertIsNotNone(legacy)
        self.assertIsNotNone(layered)
        self.assertEqual(legacy.group(1), layered.group(1))
        self.assertEqual(layered.group(1), "1048576")

    def test_script_capacity_fits_the_real_mod_sources(self):
        """真实 PC Mod 的 Lua 源码必须装得进脚本缓冲区（EID 的 `main.lua` 与语言文件）。"""
        fixture = (
            ROOT / "analysis" / "pc-mod-contract" / "romfs-preview" / "atmosphere" / "contents"
            / "010021C000B6A000" / "romfs" / "isaac_mods" / "mods"
            / "external item descriptions_836319872"
        )
        if not fixture.is_dir():
            self.skipTest(f"缺少 EID 夹具：{fixture}")
        service = (SRC / "application/mod/manifest_service.hpp").read_text(encoding="utf-8")
        capacity = int(
            re.search(r"kRomfsModScriptMaximumLength\s*=\s*(\d+)", service).group(1)
        )
        entry = fixture / "main.lua"
        self.assertTrue(entry.is_file())
        self.assertLess(entry.stat().st_size, capacity, "入口脚本必须装得进缓冲区")
        largest = max(path.stat().st_size for path in fixture.rglob("*.lua"))
        self.assertLess(largest, capacity, "require 的最大文件必须装得进缓冲区")

    def test_hook_diagnostics_reports_the_scriptless_outcome(self):
        hook = (SOURCE / "hook_manager.cpp").read_text(encoding="utf-8")
        self.assertIn("Scriptless = 5", hook)
        default_block = hook[
            hook.index("bool LoadDefaultManifestModThroughService") :
            hook.index("bool PrimeDefaultCallbacks")
        ]
        # 资源型 Mod 必须走"返回成功 + 报 5"，而不是被判失败。
        self.assertIn("loaded.value().ScriptExecuted()", default_block)
        self.assertIn("DefaultManifestFailureDetail::Scriptless", default_block)
        self.assertIn("return loaded.ok();", default_block)
        self.assertIn("RegisterMod(resolved.value().modRoot)", default_block)
        self.assertLess(
            default_block.index("RegisterMod(resolved.value().modRoot)"),
            default_block.index("service.Load(request, &failure)"),
            "内容挂载点必须在加载（含资源型）之前注册",
        )


if __name__ == "__main__":
    unittest.main()
