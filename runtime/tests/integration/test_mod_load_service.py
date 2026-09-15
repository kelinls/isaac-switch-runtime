"""Manifest 解析/读取与 Lua 驱动的宿主行为测试（全部依赖用假的 Port 注入）。

按设计 §10.3，manifest 的读取、解析与路径拼装属于 `ManifestService`，
入口脚本读取与 Lua 驱动属于 `ModLoadService`。测试刻意分成两段断言：

* `ManifestParser` 用注入的假 JSON 后端，验证“纯解析器”契约（不碰文件、不碰 Lua）；
* `ManifestService` 用假 `IContentPort`，验证读取、步骤码与三段路径；
* `ModLoadService` 只喂已经解析好的结果，验证它不再接触 manifest。
"""

import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "runtime" / "src"

DRIVER = textwrap.dedent(
    r"""
    #include "application/mod/manifest_service.hpp"
    #include "application/mod/mod_load_service.hpp"

    #include <cstdio>
    #include <cstring>
    #include <string>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;
    void Check(bool condition, const char* what) {
        if (!condition) { std::printf("FAILED_CHECK %s\n", what); ++failures; }
    }

    // --- fake JSON backend -------------------------------------------------

    struct FakeBackend {
        bool accept{true};
        const char* directory{"MuteOnPause"};
        const char* entry{"mods/MuteOnPause/main.lua"};
        int calls{0};
        std::size_t lastLength{0};

        static bool Select(const char* json, std::size_t length, ModManifest* manifest,
                           FakeBackend* self) noexcept {
            ++self->calls;
            self->lastLength = length;
            if (!self->accept || json == nullptr || manifest == nullptr) { return false; }
            std::strncpy(manifest->directory.data(), self->directory,
                         manifest->directory.size() - 1);
            std::strncpy(manifest->entry.data(), self->entry, manifest->entry.size() - 1);
            return true;
        }
    };

    FakeBackend g_backend{};

    bool BackendThunk(const char* json, std::size_t length, ModManifest* manifest) noexcept {
        return FakeBackend::Select(json, length, manifest, &g_backend);
    }

    // --- fake ports --------------------------------------------------------

    struct FakeContent final : IContentPort {
        Status readStatus{StatusCode::Ok};
        Status entryStatus{StatusCode::Ok};
        std::size_t manifestBytes{16};
        std::size_t entryBytes{8};
        bool manifestIsEmpty{false};
        bool entryIsEmpty{false};
        char lastPath[192]{};
        int manifestReads{0};

        Status Size(std::string_view, std::size_t*) noexcept override { return Status::Ok(); }

        Status Read(std::string_view path, std::uint8_t* target, std::size_t capacity,
                    std::size_t* readCount) noexcept override {
            std::strncpy(lastPath, std::string(path).c_str(), sizeof(lastPath) - 1);
            const bool manifest = path.find("manifest.json") != std::string_view::npos;
            const Status status = manifest ? readStatus : entryStatus;
            if (!status.ok()) { return status; }
            std::size_t bytes = manifest ? manifestBytes : entryBytes;
            if (manifest && manifestIsEmpty) { bytes = 0; }
            if (!manifest && entryIsEmpty) { bytes = 0; }
            if (bytes > capacity) { return Status{StatusCode::CapacityExceeded}; }
            if (!manifest && bytes != 0) { std::memset(target, 'x', bytes); }
            if (manifest) { ++manifestReads; }
            *readCount = bytes;
            return Status::Ok();
        }
    };

    struct FakeLua final : ILuaEnginePort {
        bool ready{true};
        Status loadStatus{StatusCode::Ok};
        std::uint32_t engineFailureDetail{0};
        std::size_t lastEntryLength{0};
        char lastEntryFirstByte{};
        char lastChunk[192]{};
        char lastRoot[128]{};
        int loads{0};

        Status Initialize() noexcept override { ready = true; return Status::Ok(); }
        bool IsReady() const noexcept override { return ready; }
        Status RunChunk(const LuaChunk&) noexcept override {
            return Status{StatusCode::Unsupported};
        }
        Status LoadManifestMod(const ManifestModLoad& load,
                               std::uint32_t* failureDetail) noexcept override {
            ++loads;
            if (load.entryBytes != nullptr && load.entryLength != 0) {
                lastEntryFirstByte = load.entryBytes[0];
            }
            lastEntryLength = load.entryLength;
            if (load.chunkName != nullptr) {
                std::strncpy(lastChunk, load.chunkName, sizeof(lastChunk) - 1);
            }
            if (load.modRoot != nullptr) {
                std::strncpy(lastRoot, load.modRoot, sizeof(lastRoot) - 1);
            }
            if (!loadStatus.ok() && failureDetail != nullptr) {
                *failureDetail = engineFailureDetail;
            }
            return loadStatus;
        }
        Status CallGlobal(const char*, int, int*) noexcept override { return Status::Ok(); }
        void Shutdown() noexcept override {}
    };

    struct StubBuffers {
        std::uint8_t manifest[1024]{};
        char entryPath[1056]{};
        char modRoot[288]{};
        char chunkName[1057]{};
    };

    bool ResolveInto(ManifestService& service, StubBuffers& buffers, ResolvedManifestMod* out,
                     ModLoadFailure* failure) {
        Result<ResolvedManifestMod> resolved = service.Resolve(
            buffers.manifest, sizeof(buffers.manifest),
            buffers.entryPath, sizeof(buffers.entryPath),
            buffers.modRoot, sizeof(buffers.modRoot),
            buffers.chunkName, sizeof(buffers.chunkName), failure);
        if (!resolved.ok()) { return false; }
        *out = resolved.value();
        return true;
    }

    } // namespace

    int main() {
        FakeContent content{};
        FakeLua lua{};
        ManifestService manifestService{content, ManifestParser{&BackendThunk}};
        ModLoadService loadService{content, lua};
        StubBuffers buffers{};
        ModLoadFailure failure{};

        // --- ManifestParser is a pure value type ---------------------------
        ManifestParser unconfigured{};
        Check(unconfigured.Parse("{}", 2).failure == ManifestParseFailure::InvalidArgument,
              "parser_without_backend_rejected");
        ManifestParser parser{&BackendThunk};
        Check(parser.Parse(nullptr, 0).failure == ManifestParseFailure::InvalidArgument,
              "null_json_rejected");
        Check(parser.Parse("{}", 0).failure == ManifestParseFailure::InvalidArgument,
              "zero_length_rejected");
        g_backend.accept = false;
        Check(parser.Parse("{}", 2).failure == ManifestParseFailure::InvalidJson,
              "malformed_json_mapped");
        g_backend.accept = true;
        g_backend.directory = "";
        Check(parser.Parse("{}", 2).failure == ManifestParseFailure::InvalidMod,
              "empty_directory_rejected");
        g_backend.directory = "MuteOnPause";
        g_backend.entry = "";
        // 空 entry = 纯资源 Mod（打包工具对没有 `main.lua` 的 Mod 就省略 `entry` 这个键）。
        // 这是**合法**清单：解析成功，由加载层决定"只挂内容、不初始化 Lua"。
        Check(parser.Parse("{}", 2).failure == ManifestParseFailure::None,
              "empty_entry_is_scriptless");
        Check(!parser.Parse("{}", 2).manifest.HasEntry(), "empty_entry_reports_no_script");
        g_backend.entry = "mods/MuteOnPause/main.lua";
        ManifestParseOutcome parsed = parser.Parse("{\"mods\":[]}", 10);
        Check(parsed.failure == ManifestParseFailure::None &&
                  std::strcmp(parsed.manifest.directory.data(), "MuteOnPause") == 0,
              "parse_success_returns_manifest");
        Check(g_backend.lastLength == 10, "backend_receives_length");

        // --- ManifestService: read, parse, path build ----------------------
        ResolvedManifestMod resolved{};
        Check(ResolveInto(manifestService, buffers, &resolved, &failure),
              "resolve_happy_path");
        Check(failure.step == ModLoadStep::None, "resolve_success_reports_no_failure");
        Check(std::strcmp(resolved.entryPath, "rom:/isaac_mods/mods/MuteOnPause/main.lua") == 0,
              "entry_path_built");
        Check(std::strcmp(resolved.modRoot, "rom:/isaac_mods/mods/MuteOnPause") == 0,
              "mod_root_built");
        Check(std::strcmp(resolved.chunkName, "@rom:/isaac_mods/mods/MuteOnPause/main.lua") == 0,
              "chunk_name_built");
        Check(resolved.manifestBytes == 16, "manifest_size_reported");

        content.readStatus = Status{StatusCode::NotFound};
        Check(!ResolveInto(manifestService, buffers, &resolved, &failure) &&
                  failure.step == ModLoadStep::ManifestRead,
              "manifest_read_failure_step");
        content.readStatus = Status{StatusCode::Ok};

        content.manifestIsEmpty = true;
        Check(!ResolveInto(manifestService, buffers, &resolved, &failure) &&
                  failure.step == ModLoadStep::ManifestRead,
              "empty_manifest_step");
        content.manifestIsEmpty = false;

        g_backend.accept = false;
        Check(!ResolveInto(manifestService, buffers, &resolved, &failure) &&
                  failure.step == ModLoadStep::ManifestParse,
              "parse_failure_step");
        g_backend.accept = true;

        // A path buffer that cannot hold the assembled path is a path-build
        // failure. The production capacities always fit a domain-sized
        // directory/entry, so the check is exercised through a deliberately
        // narrow buffer instead of a hostile manifest.
        {
            char narrowEntry[8]{};
            char narrowModRoot[8]{};
            char narrowChunk[8]{};
            const ModLoadFailure narrowFailure = [&] {
                ModLoadFailure recorded{};
                static_cast<void>(manifestService.Resolve(
                    buffers.manifest, sizeof(buffers.manifest),
                    narrowEntry, sizeof(narrowEntry),
                    narrowModRoot, sizeof(narrowModRoot),
                    narrowChunk, sizeof(narrowChunk), &recorded));
                return recorded;
            }();
            Check(narrowFailure.step == ModLoadStep::PathBuild, "path_overflow_step");
        }

        Check(ResolveInto(manifestService, buffers, &resolved, &failure) &&
                  failure.step == ModLoadStep::None,
              "resolve_usable_again");

        // --- ModLoadService: entry read + Lua only --------------------------
        ModLoadRequest request{};
        request.resolved = &resolved;
        request.entryBuffer = buffers.manifest;
        request.entryCapacity = sizeof(buffers.manifest);

        lua.ready = false;
        lua.loads = 0;
        lua.lastEntryLength = 0;
        lua.lastEntryFirstByte = '\0';
        lua.lastChunk[0] = '\0';
        lua.lastRoot[0] = '\0';
        Result<ModLoadOutcome> ok = loadService.Load(request, &failure);
        Check(ok.ok() && lua.loads == 1, "bootstrap_load_runs_before_engine_ready");
        lua.ready = true;
        lua.loads = 0;
        lua.lastEntryLength = 0;
        lua.lastEntryFirstByte = '\0';
        lua.lastChunk[0] = '\0';
        lua.lastRoot[0] = '\0';

        const int manifestReadsBefore = content.manifestReads;
        ok = loadService.Load(request, &failure);
        Check(ok.ok(), "happy_path_ok");
        Check(ok.ok() && ok.value().manifestBytes == 16, "manifest_size_forwarded");
        Check(ok.ok() && ok.value().entryBytes == 8, "entry_size_forwarded");
        Check(!ok.ok() || failure.step == ModLoadStep::None, "success_reports_no_failure");
        Check(content.manifestReads == manifestReadsBefore,
              "load_service_never_reads_the_manifest");
        Check(lua.loads == 1 && lua.lastEntryLength == 8 && lua.lastEntryFirstByte == 'x',
              "entry_bytes_forwarded");
        Check(std::strcmp(lua.lastChunk, "@rom:/isaac_mods/mods/MuteOnPause/main.lua") == 0,
              "chunk_name_forwarded");
        Check(std::strcmp(lua.lastRoot, "rom:/isaac_mods/mods/MuteOnPause") == 0,
              "mod_root_forwarded");
        Check(std::strcmp(content.lastPath, "rom:/isaac_mods/mods/MuteOnPause/main.lua") == 0,
              "entry_path_addressed");

        // 入口脚本在包里不存在（清单写了 `entry`，文件没打进 romfs）：**不再是整包失败**。
        // 内容挂载点已由调用方注册，所以只降级为"资源型加载"并把状态报出去。
        content.entryStatus = Status{StatusCode::NotFound};
        ok = loadService.Load(request, &failure);
        Check(ok.ok(), "missing_entry_file_loads");
        Check(ok.ok() && ok.value().scriptState == ModScriptState::EntryAbsent,
              "missing_entry_file_reports_entry_absent");
        Check(ok.ok() && ok.value().entryBytes == 0, "missing_entry_file_reports_no_entry_bytes");
        Check(!ok.ok() || failure.step == ModLoadStep::None,
              "missing_entry_file_reports_no_failure");
        content.entryStatus = Status{StatusCode::Ok};

        // 其它读失败仍然失败：容量/IO/损坏都不能被当成"文件不存在"。
        content.entryStatus = Status{StatusCode::IoFailure};
        Check(loadService.Load(request, &failure).code() == StatusCode::IoFailure,
              "entry_io_failure_propagated");
        Check(failure.step == ModLoadStep::EntryRead, "entry_io_failure_step");
        content.entryStatus = Status{StatusCode::Ok};

        content.entryIsEmpty = true;
        Check(loadService.Load(request, &failure).code() == StatusCode::Corrupted,
              "empty_entry_rejected");
        Check(failure.step == ModLoadStep::EntryRead, "empty_entry_step");
        content.entryIsEmpty = false;

        // --- 纯资源 Mod：没有 entry，只有 Mod 根 ---------------------------
        g_backend.entry = "";
        ResolvedManifestMod scriptless{};
        Check(ResolveInto(manifestService, buffers, &scriptless, &failure),
              "scriptless_resolve_ok");
        Check(!scriptless.hasEntry, "scriptless_resolution_has_no_entry");
        Check(std::strcmp(scriptless.entryPath, "") == 0, "scriptless_entry_path_is_empty");
        Check(std::strcmp(scriptless.chunkName, "") == 0, "scriptless_chunk_name_is_empty");
        Check(std::strcmp(scriptless.modRoot, "rom:/isaac_mods/mods/MuteOnPause") == 0,
              "scriptless_mod_root_built");
        {
            lua.loads = 0;
            ModLoadRequest scriptlessRequest = request;
            scriptlessRequest.resolved = &scriptless;
            ok = loadService.Load(scriptlessRequest, &failure);
            Check(ok.ok(), "scriptless_load_ok");
            Check(ok.ok() && ok.value().scriptState == ModScriptState::DeclaredScriptless,
                  "scriptless_state_reported");
            Check(ok.ok() && !ok.value().ScriptExecuted(), "scriptless_is_not_script_executed");
            Check(ok.ok() && ok.value().entryBytes == 0, "scriptless_reports_no_entry_bytes");
            Check(lua.loads == 0, "scriptless_never_initializes_lua");
            Check(failure.step == ModLoadStep::None, "scriptless_reports_no_failure");
        }
        g_backend.entry = "mods/MuteOnPause/main.lua";
        Check(ResolveInto(manifestService, buffers, &resolved, &failure),
              "scripted_resolution_restored");
        lua.loads = 0;

        lua.loadStatus = Status{StatusCode::Rejected};
        lua.engineFailureDetail = 2;
        Check(loadService.Load(request, &failure).code() == StatusCode::Rejected,
              "lua_failure_propagated");
        Check(failure.step == ModLoadStep::LuaInit, "lua_failure_step");
        Check(failure.detail == 2, "lua_failure_detail_forwarded");
        lua.loadStatus = Status{StatusCode::Ok};
        lua.engineFailureDetail = 0;

        ModLoadRequest bad = request;
        bad.resolved = nullptr;
        Check(loadService.Load(bad, &failure).code() == StatusCode::InvalidArgument,
              "missing_resolution_rejected");
        Check(failure.step == ModLoadStep::Request, "request_failure_step_reported");
        bad = request;
        bad.entryBuffer = nullptr;
        Check(loadService.Load(bad).code() == StatusCode::InvalidArgument,
              "null_entry_buffer_rejected");
        bad = request;
        bad.entryCapacity = 0;
        Check(loadService.Load(bad).code() == StatusCode::InvalidArgument,
              "zero_entry_capacity_rejected");

        // A resolution that never produced paths must not be loadable.
        ResolvedManifestMod empty{};
        bad = request;
        bad.resolved = &empty;
        Check(loadService.Load(bad).code() == StatusCode::InvalidArgument,
              "unresolved_paths_rejected");

        if (failures != 0) { std::printf("MOD_LOAD_CHECKS_FAILED %d\n", failures); return 1; }
        std::printf("MOD_LOAD_CHECKS_PASSED\n");
        return 0;
    }
    """
)


def host_compiler() -> str | None:
    for candidate in ("c++", "clang++", "g++"):
        found = shutil.which(candidate)
        if found is not None:
            return found
    return None


class ModLoadServiceTests(unittest.TestCase):
    def test_load_service_never_gates_on_engine_readiness(self):
        """回归护栏：加载用例本身就是把引擎变成 ready 的那一步。

        这里曾在接线时保留 `IsReady()` 前置检查，导致分层包首次加载直接
        返回 `InvalidState`，Mod 静默失效（真机表现为“mod 没生效”）。
        """
        source = (SRC / "application" / "mod" / "mod_load_service.cpp").read_text(encoding="utf-8")
        body = source.split("Result<ModLoadOutcome> ModLoadService::Load", 1)[1]
        self.assertNotIn("IsReady", body.split("} // namespace", 1)[0])
        port = (SRC / "ports" / "lua_engine_port.hpp").read_text(encoding="utf-8")
        self.assertIn("must not require a ready engine", port)

    def test_manifest_package_owns_reading_and_parsing(self):
        """设计 §10.3：Manifest 读取/解析不再放在 Lua 驱动服务里。"""
        load = (SRC / "application" / "mod" / "mod_load_service.cpp").read_text(encoding="utf-8")
        service = (SRC / "application" / "mod" / "manifest_service.cpp").read_text(encoding="utf-8")
        parser = (SRC / "application" / "mod" / "manifest_parser.cpp").read_text(encoding="utf-8")
        # 旧的一次性选择接口已被 ManifestParser 取代。
        self.assertFalse((SRC / "application" / "mod" / "manifest_selector.hpp").exists())
        # Lua 驱动侧不得再出现 manifest 字样或旧接口名。
        for forbidden in ("manifest.json", "ManifestSelect", "IManifestSelector", "manifestBuffer"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, load)
        # 读取与选择在 ManifestService，解析在 ManifestParser（纯值类型）。
        self.assertIn("content_.Read(kRomfsModManifestPath", service)
        self.assertIn("parser_.Parse(", service)
        self.assertIn("ModLoadStep::ManifestParse", service)
        self.assertIn("ModLoadStep::PathBuild", service)
        self.assertNotIn("content_", parser)
        self.assertNotIn("lua", parser)
        # 步骤码是设备端线格式，必须与旧 DefaultManifestFailureDetail 相同。
        steps = (SRC / "application" / "mod" / "mod_load_step.hpp").read_text(encoding="utf-8")
        for line in ("ManifestRead = 1", "ManifestParse = 2", "PathBuild = 3",
                     "EntryRead = 4", "LuaInit = 0x10"):
            with self.subTest(step=line):
                self.assertIn(line, steps)

    def test_manifest_selector_adapter_is_the_only_legacy_bridge(self):
        """`ManifestParser` 不得依赖旧 `::ModManifest`，桥接只允许在适配器里。"""
        adapter = (SRC / "infrastructure" / "mod" / "manifest_selector_adapter.cpp").read_text(
            encoding="utf-8"
        )
        parser = (SRC / "application" / "mod" / "manifest_parser.cpp").read_text(encoding="utf-8")
        header = (SRC / "application" / "mod" / "manifest_parser.hpp").read_text(encoding="utf-8")
        self.assertIn('#include "mod_manifest.hpp"', adapter)
        self.assertIn("::ModManifest::SelectFirstEnabled", adapter)
        self.assertIn("ManifestSelectFunction ManifestSelectorAdapter::SelectFunction()", adapter)
        # 解析器只认 domain 里的 `ModManifest`，不得引用旧 `::ModManifest` 或旧根头。
        for source in (parser, header):
            with self.subTest(source=source[:24]):
                self.assertNotIn('#include "mod_manifest.hpp"', source)
                self.assertNotIn("::ModManifest", source)

    def test_load_flow_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-mod-load-") as temporary:
            directory = Path(temporary)
            source = directory / "mod_load.cpp"
            binary = directory / "mod_load"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
                    "-I", str(SRC),
                    str(source),
                    str(SRC / "application" / "mod" / "manifest_parser.cpp"),
                    str(SRC / "application" / "mod" / "manifest_service.cpp"),
                    str(SRC / "application" / "mod" / "mod_load_service.cpp"),
                    "-o", str(binary),
                ],
                capture_output=True, text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("MOD_LOAD_CHECKS_PASSED", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
