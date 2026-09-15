"""统一 API Catalog 的契约测试。

Catalog 必须是 Lua API 的唯一清单：每个真实注册的 API 都能在清单里找到，
清单里也不能出现运行时不存在的“幽灵 API”。同时校验 id 方案、重复项与查询行为。
"""

import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
SRC = RUNTIME / "src"
LUA_RUNTIME = RUNTIME / "source" / "lua_runtime.cpp"
CATALOG_SOURCE = SRC / "interfaces" / "lua" / "api_catalog.cpp"
FAMILY_API_SOURCES = (
    SRC / "interfaces" / "lua" / "mod_api.cpp",
    SRC / "interfaces" / "lua" / "game_api.cpp",
    SRC / "interfaces" / "lua" / "isaac_api.cpp",
    SRC / "interfaces" / "lua" / "remaining_api.cpp",
    SRC / "interfaces" / "lua" / "music_api.cpp",
    SRC / "interfaces" / "lua" / "rng_api.cpp",
    SRC / "interfaces" / "lua" / "input_api.cpp",
    SRC / "interfaces" / "lua" / "font_api.cpp",
    SRC / "interfaces" / "lua" / "color_api.cpp",
    SRC / "interfaces" / "lua" / "vector_api.cpp",
    SRC / "interfaces" / "lua" / "sprite_api.cpp",
)

DRIVER = textwrap.dedent(
    r"""
    #include "domain/runtime/status.hpp"
    #include "interfaces/lua/api_catalog.hpp"

    #include <cstdio>
    #include <cstring>

    using namespace isaac::runtime;

    namespace {

    int failures = 0;

    void Check(bool condition, const char* what) {
        if (!condition) {
            std::printf("FAILED_CHECK %s\n", what);
            ++failures;
        }
    }

    constexpr LuaApiDescriptor InvalidEntry(std::uint32_t id, ApiDomain domain, const char* owner,
                                            const char* name) {
        return LuaApiDescriptor{id, domain, owner, name, ApiVersion{1, 0}, 0,
                                ThreadAffinity::Any, ApiMaturity::Experimental};
    }

    void TestDefaultCatalog() {
        const ApiCatalog& catalog = ApiCatalog::Default();
        Check(catalog.Validate().ok(), "default_catalog_valid");
        Check(catalog.Count() >= 30, "catalog_covers_current_surface");

        const LuaApiDescriptor* paused = catalog.Find(0x02010001);
        Check(paused != nullptr && std::strcmp(paused->owner, "Game") == 0 &&
                  std::strcmp(paused->name, "IsPaused") == 0,
              "find_by_id_game_ispaused");
        Check(paused != nullptr && paused->maturity == ApiMaturity::HardwareVerified,
              "ispaused_is_hardware_verified");
        Check(paused != nullptr && paused->domain == ApiDomain::Game, "ispaused_domain");

        const LuaApiDescriptor* pause = catalog.Find("MusicManager", "Pause");
        Check(pause != nullptr && pause->id == 0x06010002, "find_pause_by_owner_name");
        Check(pause != nullptr && pause->affinity == ThreadAffinity::MainRender,
              "pause_runs_on_render_thread");

        const LuaApiDescriptor* save = catalog.Find("Mod", "SaveData");
        Check(save != nullptr && save->id == 0x08010001, "save_data_id");
        Check(save != nullptr && save->maturity == ApiMaturity::Experimental,
              "persistence_stays_experimental");

        const LuaApiDescriptor* registerMod = catalog.Find("Global", "RegisterMod");
        Check(registerMod != nullptr && registerMod->id == 0x00010001, "register_mod_id");

        Check(catalog.Find(static_cast<std::uint32_t>(0xDEADBEEF)) == nullptr, "unknown_id_missing");
        Check(catalog.Find("Game", "NotAnApi") == nullptr, "unknown_name_missing");
        Check(catalog.Find(nullptr, "IsPaused") == nullptr, "null_owner_rejected");
        Check(catalog.Find("Game", nullptr) == nullptr, "null_name_rejected");
        Check(catalog.At(catalog.Count()) == nullptr, "at_out_of_range");
    }

    void TestValidationRejectsBadTables() {
        const LuaApiDescriptor duplicateId[] = {
            InvalidEntry(0x02010001, ApiDomain::Game, "Game", "IsPaused"),
            InvalidEntry(0x02010001, ApiDomain::Game, "Game", "IsOther"),
        };
        Check(ApiCatalog{duplicateId, 2}.Validate().code() == StatusCode::Rejected,
              "duplicate_id_rejected");

        const LuaApiDescriptor duplicateName[] = {
            InvalidEntry(0x02010001, ApiDomain::Game, "Game", "IsPaused"),
            InvalidEntry(0x02010002, ApiDomain::Game, "Game", "IsPaused"),
        };
        Check(ApiCatalog{duplicateName, 2}.Validate().code() == StatusCode::Rejected,
              "duplicate_owner_name_rejected");

        const LuaApiDescriptor emptyName[] = {
            InvalidEntry(0x02010001, ApiDomain::Game, "Game", ""),
        };
        Check(ApiCatalog{emptyName, 1}.Validate().code() == StatusCode::InvalidArgument,
              "empty_name_rejected");

        const LuaApiDescriptor zeroId[] = {
            InvalidEntry(0, ApiDomain::Game, "Game", "IsPaused"),
        };
        Check(ApiCatalog{zeroId, 1}.Validate().code() == StatusCode::InvalidArgument,
              "zero_id_rejected");

        // Domain byte must match the declared domain: an id from another domain
        // silently mislabels the API in generated docs.
        const LuaApiDescriptor wrongDomain[] = {
            InvalidEntry(0x06010001, ApiDomain::Game, "Game", "IsPaused"),
        };
        Check(ApiCatalog{wrongDomain, 1}.Validate().code() == StatusCode::InvalidArgument,
              "domain_byte_mismatch_rejected");

        Check(ApiCatalog{nullptr, 0}.Validate().code() == StatusCode::InvalidArgument,
              "empty_catalog_rejected");
    }

    } // namespace

    int main() {
        TestDefaultCatalog();
        TestValidationRejectsBadTables();
        if (failures != 0) {
            std::printf("CATALOG_CHECKS_FAILED %d\n", failures);
            return 1;
        }
        std::printf("CATALOG_CHECKS_PASSED\n");
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


def non_layered_regions(source: str) -> list[str]:
    """Return every `#if !defined(EXL_LAYERED_RUNTIME)` block of a source file.

    The legacy translation unit keeps a verbatim copy of each migrated handler
    inside such a block, so the tests need the block *bodies*. A plain regex
    cannot do this: blocks like the Music family contain nested
    `#if defined(EXL_DIAGNOSTIC_STAGE)` directives, and a non-greedy match would
    stop at the inner `#endif` and silently report the body as missing.
    """
    regions: list[str] = []
    depth = 0
    start: int | None = None
    for index, line in enumerate(source.splitlines()):
        stripped = line.strip()
        if stripped == "#if !defined(EXL_LAYERED_RUNTIME)":
            if depth == 0:
                start = index + 1
            depth += 1
            continue
        if stripped.startswith("#if"):
            depth += 1
            continue
        if stripped.startswith("#endif"):
            depth = max(0, depth - 1)
            if depth == 0 and start is not None:
                regions.append("\n".join(source.splitlines()[start:index]))
                start = None
    return regions


def registered_function_names() -> set[str]:
    """Extract every Lua function field the runtime registers."""
    lines = LUA_RUNTIME.read_text(encoding="utf-8").splitlines()
    names: set[str] = set()
    for index, line in enumerate(lines):
        match = re.search(r'lua_setfield\(state, -2, "([^"]+)"\)', line)
        if match is None:
            continue
        name = match.group(1)
        if name.startswith("__"):
            continue  # metatable plumbing, not a callable API
        previous = lines[index - 1] if index > 0 else ""
        if "lua_pushcfunction" in previous:
            names.add(name)

    registered_ids = set()
    for source_path in FAMILY_API_SOURCES:
        registered_ids.update(
            re.findall(
                r"\{\s*(0x[0-9A-Fa-f]{8}),\s*&",
                source_path.read_text(encoding="utf-8"),
            )
        )
    domain_bytes = {
        "Global": 0, "Mod": 1, "Game": 2, "Level": 3, "Room": 4,
        "ItemPool": 5, "Music": 6, "Rng": 7, "Persistence": 8, "Input": 9,
        "Diagnostic": 10, "Font": 11, "Vector": 12, "Sprite": 13, "Vector": 12,
        "Isaac": 14,
        # `Seeds`（`Game:GetSeeds()` 的返回值），2026-09-12 第五轮加入。这份映射与下面
        # `test_family_binding_ids_match_the_catalog` 里的那份必须同时加，否则新家族会被
        # 静默跳过 —— 两个字典各写一份本身就是个坑，注释在此留痕。
        "Seed": 15,
    }
    catalog = CATALOG_SOURCE.read_text(encoding="utf-8")
    for domain, group, sequence, name in re.findall(
        r'MakeId\(ApiDomain::(\w+),\s*(\d+),\s*(0x[0-9A-Fa-f]+)\),\s*'
        r'ApiDomain::\w+,\s*"[^"]+",\s*"([^"]+)"',
        catalog,
    ):
        if domain not in domain_bytes:
            continue
        identifier = (
            (domain_bytes[domain] << 24) | (int(group) << 16) | int(sequence, 16)
        )
        if f"0x{identifier:08x}" in {value.lower() for value in registered_ids}:
            names.add(name)
    return names


def registered_global_names() -> set[str]:
    """Every global name the runtime (or a family TU) publishes.

    家族 TU 也会挂全局：`Isaac`/`Options` 由 `isaac_api.cpp` 自己建表并 `lua_setglobal`，
    批次 4 的 `GetPtrHash` 同理。Catalog 的幽灵条目门禁必须看得见它们，否则一条真实现
    会被判成"登记了但没注册"。
    """
    names = set(
        re.findall(r'lua_setglobal\(state, "([^"]+)"\)', LUA_RUNTIME.read_text(encoding="utf-8"))
    )
    for source_path in FAMILY_API_SOURCES:
        names.update(
            re.findall(r'lua_setglobal\(state, "([^"]+)"\)',
                       source_path.read_text(encoding="utf-8"))
        )
    return names


def catalog_pairs() -> set[tuple[str, str]]:
    """(owner, name) pairs from the catalog table."""
    source = CATALOG_SOURCE.read_text(encoding="utf-8")
    # Entries start with `{MakeId(...)`; the first two quoted strings inside an
    # entry are the owner label and the API name.
    return set(
        re.findall(
            r'MakeId\([^}]*?"([A-Za-z_][A-Za-z0-9_]*)",\s*"([A-Za-z_][A-Za-z0-9_]*)"',
            source,
            flags=re.DOTALL,
        )
    )


def catalog_api_names() -> set[str]:
    return {name for _owner, name in catalog_pairs()}


def catalog_owner_labels() -> set[str]:
    return {owner for owner, _name in catalog_pairs()}


class ApiCatalogContractTests(unittest.TestCase):
    def test_mod_methods_are_registered_from_their_owner_translation_unit(self):
        """`Mod` 家族的绑定表、注册与实现体都在自己的 TU 里。

        `Mod:AddCallback` 是最后一个搬出旧 TU 的实现体（回调注册表通过
        `lua_runtime_state.hpp` 的访问器取得），因此 `lua_runtime.cpp` 里不得再出现
        任何 Mod handler、过渡 thunk 或过渡头。
        """
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        mod_api = (SRC / "interfaces" / "lua" / "mod_api.cpp").read_text(encoding="utf-8")
        self.assertIn("isaac::runtime::AttachModMethods(state)", source)
        self.assertIn("std::size_t AttachModMethods(lua_State* state) noexcept", mod_api)
        self.assertIn("AttachOwnerMethods(state, kModOwner", mod_api)
        for api_id in ("0x01010001", "0x08010001", "0x08010002", "0x08010003", "0x08010004"):
            with self.subTest(api_id=api_id):
                self.assertIn(api_id, mod_api, f"{api_id} 缺少 Mod 绑定行")
        # 五个 handler 的实现体都在家族 TU，绑定表直接取本 TU 的函数地址。
        for handler in ("ModAddCallback", "ModSaveData", "ModLoadData", "ModHasData",
                        "ModRemoveData"):
            with self.subTest(handler=handler):
                self.assertIn(f"int {handler}(lua_State* state) {{", mod_api)
                self.assertIn(f"&{handler}", mod_api)
                self.assertNotIn(f"int {handler}(lua_State* state) {{", source)
        # 过渡头与过渡 thunk 都已删除。
        self.assertFalse((SRC / "interfaces" / "lua" / "legacy_handler_bindings.hpp").exists())
        self.assertNotIn("LuaModAddCallback", mod_api)
        self.assertNotIn("LuaModAddCallback", source)
        # 回调注册表仍由旧 TU 拥有，家族 TU 只通过访问器取用。
        state = (ROOT / "runtime" / "source" / "lua_runtime_state.hpp").read_text(encoding="utf-8")
        for accessor in ("ManagedCallbackRegistry", "RuntimeOwnerHandle", "IsStage13CallbackMode"):
            with self.subTest(accessor=accessor):
                self.assertIn(accessor, state)
                self.assertIn(accessor, mod_api)
        self.assertIn("CallbackRegistry& ManagedCallbackRegistry()", source)

    def test_game_methods_are_registered_from_their_owner_translation_unit(self):
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        game_api = (SRC / "interfaces" / "lua" / "game_api.cpp").read_text(encoding="utf-8")
        shared = (SRC / "interfaces" / "lua" / "owner_binding.cpp").read_text(encoding="utf-8")
        self.assertIn("isaac::runtime::AttachGameMethods(state)", source)
        self.assertIn("std::size_t AttachGameMethods(lua_State* state) noexcept", game_api)
        self.assertIn("AttachOwnerMethods(state, kGameOwner", game_api)
        for api_id in ("0x02010001", "0x02010002", "0x02010003", "0x02010004", "0x02010005"):
            with self.subTest(api_id=api_id):
                self.assertIn(api_id, game_api, f"{api_id} 缺少 Game 绑定行")
        # 注册循环只有一份实现，家族 TU 只放绑定表。
        self.assertIn("std::size_t AttachOwnerMethods(", shared)
        self.assertIn("catalog.At(index)", shared)
        for family in ("mod_api.cpp", "game_api.cpp"):
            body = (SRC / "interfaces" / "lua" / family).read_text(encoding="utf-8")
            self.assertNotIn("lua_setfield", body, f"{family} 不应重复注册循环")

    def test_family_handler_bodies_live_in_their_family_units(self):
        """实现体归属：每个 handler 只允许存在于自己的家族 TU。

        第十片删除了旧 TU 的非分层副本，所以这里同时断言"旧 TU 里没有任何副本"，
        以及每个 handler 都能在自己的家族 TU 里找到唯一一份实现体。
        """
        legacy = LUA_RUNTIME.read_text(encoding="utf-8")
        self.assertEqual(non_layered_regions(legacy), [],
                         "旧 TU 仍保留非分层副本，探针与生产包会走两套实现")
        owners = {
            "mod_api.cpp": ("ModAddCallback", "ModSaveData", "ModLoadData", "ModHasData",
                            "ModRemoveData"),
            "game_api.cpp": ("GameIsPaused", "GameIsGreedMode", "GameGetLevel",
                             "GameGetItemPool", "GameGetRoom"),
            "remaining_api.cpp": ("LevelGetStage", "LevelIsAscent", "RoomGetType",
                                  "ItemPoolGetCollectible"),
            "music_api.cpp": ("MusicGetCurrentMusicId", "MusicPause", "MusicResume"),
            "rng_api.cpp": ("RngSetSeed", "RngNext"),
            "input_api.cpp": ("InputIsButtonTriggered", "InputIsButtonPressed",
                              "InputGetButtonValue", "InputIsActionTriggered",
                              "InputIsActionPressed", "InputGetActionValue"),
            "font_api.cpp": ("FontLoad", "FontUnload", "FontIsLoaded", "FontGetStringWidth",
                             "FontGetStringWidthUTF8", "FontGetLineHeight",
                             "FontGetBaselineHeight", "FontGetCharacterWidth",
                             "FontSetMissingCharacter"),
            "vector_api.cpp": ("VectorLength", "VectorLengthSquared", "VectorDistance",
                               "VectorDistanceSquared", "VectorDot", "VectorCross",
                               "VectorGetAngleDegrees", "VectorNormalize", "VectorNormalized",
                               "VectorResize", "VectorResized", "VectorRotated", "VectorClamp",
                               "VectorClamped", "VectorLerp", "VectorFromAngle"),
            "sprite_api.cpp": ("SpritePlay", "SpriteSetAnimation", "SpriteSetFrame",
                               "SpriteLoad", "SpriteLoadGraphics", "SpriteReplaceSpritesheet",
                               "SpriteGetFrame", "SpriteSetLayerFrame",
                               "SpriteGetLayerFrame", "SpriteUpdate", "SpriteIsPlaying",
                               "SpriteIsFinished", "SpritePlayRandom", "SpriteRender",
                               "SpriteRenderLayer", "SpriteIsLoaded"),
            # `Isaac` 门面：19 个成员都在自己的家族 TU 里（`Options` 的两个字段是值，
            # 不是 handler，所以不在这张表里）。
            "isaac_api.cpp": ("IsaacGetFrameCount", "IsaacGetTime", "IsaacDebugString",
                              "IsaacIsInGame", "IsaacRunCallback", "IsaacGetPlayer",
                              "IsaacGetItemConfig", "IsaacFindByType", "IsaacFindInRadius",
                              "IsaacCountEnemies", "IsaacCountBosses", "IsaacWorldToScreen",
                              "IsaacWorldToRenderPosition", "IsaacGetPersistentGameData",
                              "IsaacGetTrinketIdByName", "IsaacGetCallbacks", "IsaacLoadModData",
                              "IsaacSaveModData", "IsaacRenderScaledText"),
        }
        for unit, handlers in owners.items():
            body = (SRC / "interfaces" / "lua" / unit).read_text(encoding="utf-8")
            for handler in handlers:
                with self.subTest(unit=unit, handler=handler):
                    self.assertEqual(body.count(f"int {handler}(lua_State* state) {{"), 1)
                    self.assertNotIn(f"int {handler}(lua_State* state) {{", legacy,
                                     f"{handler} 的实现体同时存在于旧 TU")
        # 旧 TU 也不再保留任何内联注册语句。
        for handler in ("GameIsPaused", "RoomGetType", "ItemPoolGetCollectible", "LevelGetStage",
                        "MusicGetCurrentMusicId", "RngSetSeed", "InputIsButtonTriggered",
                        "AddCallback"):
            with self.subTest(handler=handler):
                self.assertNotIn(f"lua_pushcfunction(state, {handler})", legacy)

    def test_every_owner_registers_from_a_family_unit(self):
        """所有 owner 都从家族 TU 注册，旧 TU 不再自带内联 switch/注册循环。"""
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        expected = {
            "Mod": ("mod_api.cpp", "AttachModMethods"),
            "Game": ("game_api.cpp", "AttachGameMethods"),
            "Level": ("remaining_api.cpp", "AttachLevelMethods"),
            "Room": ("remaining_api.cpp", "AttachRoomMethods"),
            "ItemPool": ("remaining_api.cpp", "AttachItemPoolMethods"),
            "MusicManager": ("music_api.cpp", "AttachMusicMethods"),
            "RNG": ("rng_api.cpp", "AttachRngMethods"),
            "Input": ("input_api.cpp", "AttachInputMethods"),
            "Font": ("font_api.cpp", "AttachFontMethods"),
            # `Vector` 的整套注册（元表 + 运算符 + 可调用的类表）在自己的 TU 里，
            # 所以 `lua_runtime.cpp` 通过 `RegisterVectorApi` 间接调用家族注册函数。
            "Vector": ("vector_api.cpp", "AttachVectorMethods"),
            "Sprite": ("sprite_api.cpp", "AttachSpriteMethods"),
            # `Isaac` 的全局表也由家族 TU 自己建立（`RegisterIsaacApi` 里调
            # `AttachIsaacMethods`），旧 TU 只调用家族入口。
            "Isaac": ("isaac_api.cpp", "AttachIsaacMethods"),
        }
        # 家族自己建表的 owner：旧 TU 里出现的是家族入口名，`Attach*Methods` 只在本 TU。
        family_entry_points = {
            "Isaac": ("isaac::runtime::RegisterIsaacApi(state)", "AttachIsaacMethods(state)"),
        }
        for owner, (unit, function) in expected.items():
            with self.subTest(owner=owner):
                body = (SRC / "interfaces" / "lua" / unit).read_text(encoding="utf-8")
                if owner in family_entry_points:
                    entry_point, attach_call = family_entry_points[owner]
                    self.assertIn(entry_point, source)
                    self.assertIn(attach_call, body)
                else:
                    self.assertIn(f"isaac::runtime::{function}(state)", source)
                self.assertIn(f"std::size_t {function}(lua_State* state) noexcept", body)
                # owner 既可以直接写字面量，也可以通过常量传入。
                self.assertIn("AttachOwnerMethods(state,", body)
                self.assertIn(f'"{owner}"', body)
        self.assertNotIn("AttachCatalogOwnerMethods", source)
        self.assertNotIn("ResolveLuaApiHandler", source)

    def test_family_binding_ids_match_the_catalog(self):
        """家族表里的字面 id 必须与 Catalog 计算出的 id 一致。

        id 对不上时方法只是“静默不注册”，行为测试不一定发现，所以在这里逐条比对。
        """
        domain_bytes = {
            "Global": 0, "Mod": 1, "Game": 2, "Level": 3, "Room": 4,
            "ItemPool": 5, "Music": 6, "Rng": 7, "Persistence": 8, "Input": 9,
            "Diagnostic": 10, "Font": 11, "Vector": 12, "Sprite": 13, "Isaac": 14,
            # `Seeds` 家族（`Game:GetSeeds()` 的返回值），2026-09-12 第五轮加入。
            "Seed": 15,
        }
        catalog = CATALOG_SOURCE.read_text(encoding="utf-8")
        entry = re.compile(
            r'MakeId\(ApiDomain::(\w+),\s*(\d+),\s*(0x[0-9A-Fa-f]+)\),\s*'
            r'ApiDomain::\w+,\s*"([^"]+)",\s*"([^"]+)"'
        )
        by_owner: dict[str, set[int]] = {}
        for domain, group, sequence, owner, _name in entry.findall(catalog):
            if domain not in domain_bytes:
                continue
            identifier = (domain_bytes[domain] << 24) | (int(group) << 16) | int(sequence, 16)
            by_owner.setdefault(owner, set()).add(identifier)

        # 家族表既可以直接引用本 TU 的 handler，也可以通过过渡 thunk 引用旧 TU。
        row = re.compile(r"\{\s*(0x[0-9A-Fa-f]{8}),\s*&(?:LuaRuntime::)?(\w+)\s*\}")
        unit_owners = {
            "mod_api.cpp": {"Mod"},
            # `Game` 之外还有 `Seeds`（`Game:GetSeeds()` 的返回值，2026-09-12 第五轮加入）：
            # 它的两个方法就落在同一个 TU 里，因为句柄与元表都跟着 `Game` 家族走。
            "game_api.cpp": {"Game", "Seeds"},
            "remaining_api.cpp": {"Level", "Room", "ItemPool"},
            "music_api.cpp": {"MusicManager"},
            "rng_api.cpp": {"RNG"},
            "input_api.cpp": {"Input"},
            "font_api.cpp": {"Font"},
            "color_api.cpp": {"KColor"},
            "vector_api.cpp": {"Vector"},
            "sprite_api.cpp": {"Sprite"},
            # `Isaac` 门面（19 行）以及批次 2 的实体只读视图：`Entity`/`EntityPlayer` 的
            # 方法表也在同一个家族 TU 里（元表内的 owner 就是元表名）。批次 2b 加上
            # `ItemConfig`（6 行）与 `ItemConfig_Item`（1 行）；条目的字段
            # （`ID`/`Type`/`Name`/`Description`）走 `__index`，与 `Vector` 的 `X`/`Y` 同例，
            # 不占绑定行。
            "isaac_api.cpp": {"Isaac", "Entity", "EntityPlayer", "EntityPickup",
                              "ItemConfig", "ItemConfig_Item"},
        }
        checked = 0
        for unit in ("mod_api.cpp", "game_api.cpp", "remaining_api.cpp",
                     "music_api.cpp", "rng_api.cpp", "input_api.cpp", "font_api.cpp",
                     "color_api.cpp", "vector_api.cpp", "sprite_api.cpp", "isaac_api.cpp"):
            body = (SRC / "interfaces" / "lua" / unit).read_text(encoding="utf-8")
            for identifier, _handler in row.findall(body):
                value = int(identifier, 16)
                with self.subTest(unit=unit, api_id=identifier):
                    owners = {owner for owner, ids in by_owner.items() if value in ids}
                    self.assertTrue(
                        owners & unit_owners[unit],
                        f"{identifier} 不属于 {unit} 的 owner（实际 {sorted(owners)}）",
                    )
                checked += 1
        # 35 + `Font` 的 3 个绘制变体 + `Vector` 的 16 行方法
        # （`Vector` 的 X/Y 是字段、运算符在元表里，都不占绑定行）。
        # 54 + `Sprite` 的 16 行（第一步 13 行、第二步 3 行；`SetFrame` 的两个重载合成一行）。
        # 70 + `Isaac` 的 19 行（`Options` 的两个字段是值、没有绑定行，见
        # `test_options_fields_are_registered_as_values`）。
        # 89 + 批次 2 的实体视图 4 行：`Entity.ToPlayer`（1 行）与 `EntityPlayer` 的
        # `HasCollectible`/`GetPlayerType`/`GetData`（3 行）；`EntityPlayer` 的字段
        # （`Position`/`Type`/... ）走 `__index`，与 `Vector` 的 `X`/`Y` 同例，不占绑定行。
        # 93 + 批次 2b 的 ItemConfig 视图 7 行：`ItemConfig` 的
        # `GetCollectible`/`GetTrinket`/`GetCard`/`GetPillEffect`/`IsCollectible`/`HasTags`
        # （6 行）与 `ItemConfig_Item.HasTags`（1 行）；条目的字段
        # （`ID`/`Type`/`Name`/`Description`）同样走 `__index`，不占绑定行。
        # 100 + 批次 3 的 7 行：`Game` 的 `GetNumPlayers`/`GetFrameCount`（2 行，EID 的
        # `OnRender` 早期就要用）、`EntityPlayer` 的
        # `GetOtherTwin`/`GetEffectiveMaxHearts`/`GetSoulHearts`/`GetBrokenHearts`（4 行）与
        # `ItemConfig_Item.IsCollectible`（1 行，EID 在描述构建里无参调用它）；
        # `EntityPlayer.ControllerIndex` 与全局 `Color(...)` / `debug.getinfo` 不占绑定行
        # （前者是 `__index` 里的字段名，后两者在旧 TU 里注册，见 `test_api_catalog` 的
        # "每个注册名都必须在 Catalog 里"那条）。
        # 107 + 批次 4 的 7 行：`Entity` 的 `ToPickup`/`GetData`（2 行）、`EntityPlayer` 的
        # `GetActiveItem`/`GetTrinket`/`GetBabySkin`（3 行，EID 的玩家取值路径）、
        # `EntityPickup.IsShopItem`（1 行，安全 stub）与 `Level.GetCurses`（1 行）。
        # 全局 `GetPtrHash` 在家族 TU 里 `lua_setglobal` 注册、没有绑定行（与 `Color(...)` 同例）。
        # 114 + 批次 5 的 2 行：`Entity.GetSprite`（`Entity` 主 sprite 的只读视图，EID 的
        # `EID:IsAltChoice()` 在宝藏房里必需）与 `Sprite.GetAnimation`（读 `ANM2+0x38` 的当前
        # 动画名）。两者都是真实现，各有绑定行。
        # 116 + 第五轮的 4 行：`Game.GetSeeds`/`Game.GetVictoryLap`（2 行）与
        # `Seeds.IsCustomRun`/`Seeds.GetStartSeed`（2 行）—— EID 的
        # `features/eid_api.lua:2046` 直接 `game:GetSeeds():IsCustomRun()`，
        # 缺一个就让整条 update 回调报错（真机报告 `01789210180`）。
        # 120 + 批次 6（2026-09-12）的 30 行 `EntityPlayer` 成员：EID 逐帧调用
        # （`eid_api.lua:2606` 的 `player:GetPill(0)` 等），缺一个就断整条 update 回调。
        # 150 + `Sprite.GetTexel`（1 行，2026-09-12：EID 的 `IsAltChoice` 逐像素比对要用，
        # 缺它就是"调 nil"、整条渲染链被摘除）。
                # 151 + 批次 7 的 6 行 `Room` 网格成员（2026-09-12）。
        # 158 + 2026-09-14 的 1 行 `EntityPlayer.AddCollectible`（真实现：底座
        # `Entity_Player::AddCollectible` @ `0x292A74`，调用点做入口 4 字节指纹核对，
        # 返回值用 `HasCollectible` 复核实际结果）。
        # 159 + 批次 8（2026-09-15）的 4 行 `Level` 成员：`GetCurrentRoomIndex`/`GetCurrentRoom`
        # （字段读与"与 Game:GetRoom 同一个对象"）、`GetAbsoluteStage`/`IsNextStageAvailable`
        # （各有 Switch 符号 @ `0x3E7F3C` / `0x3DBDBC`，入口 16 字节守卫校验后调用）。
        # 缺口来自 `tools/eid_api_gap_report.py`：EID 的潘多拉魔盒条目
        # （`features/eid_modifiers.lua:197/200`）在描述构建里无条件调用 `GetAbsoluteStage()`，
        # 缺它就是 "attempt to call a nil value"、整条描述回调在那一行中断。
        self.assertEqual(checked, 163, "家族绑定行数应与已登记 API 数一致")

    def test_options_fields_are_registered_as_values(self):
        """`Options` 的两个字段是**值**而不是方法：Catalog 里有条目、绑定表里没有绑定行。

        它们没有 `lua_CFunction`，所以 `test_family_binding_ids_match_the_catalog` 覆盖不到；
        这里直接断言 `RegisterOptionsTable` 真的让两个字段可读，否则 Catalog 里就多出
        两条幽灵 API。

        **两个字段的取数方式刻意不同**（按 2026-09-14 的实现改写，原断言是"两个名字都必须以
        `lua_setfield(state, -2, "<名字>")` 出现"，那是 `Language` 还被当静态值缓存时的写法）：

        * `HUDOffset` 是**静态值**：建表时一次性 `lua_setfield` 写进去；
        * `Language` 是**动态字段**：引擎中途换语言后必须能读到新值，所以不能在建表时缓存，
          改由元表 `__index`（`OptionsIndex`）每次现读
          （`ReadCurrentLanguageCode` → `Manager::GetLanguageCode()`，读不到时兜底 `"en"`）。

        因此这里按两条路径分别断言，并**反过来断言 `Language` 不得被静态缓存** —— 那正是
        这条动态设计要防的失效模式。
        """
        source = LUA_RUNTIME.read_text(encoding="utf-8")
        family = (SRC / "interfaces" / "lua" / "isaac_api.cpp").read_text(encoding="utf-8")
        self.assertIn("isaac::runtime::RegisterOptionsTable(state)", source)
        self.assertIn("std::size_t RegisterOptionsTable(lua_State* state) noexcept", family)
        labels = catalog_owner_labels()
        self.assertIn("Options", labels)
        for name in ("HUDOffset", "Language"):
            with self.subTest(field=name):
                self.assertIn(name, catalog_api_names())

        # ① 静态值：建表时直接写入。
        self.assertIn('lua_setfield(state, -2, "HUDOffset")', family)
        # ② 动态字段：元表 `__index` 指向 `OptionsIndex`，且它确实服务 `Language`。
        self.assertIn("lua_pushcfunction(state, OptionsIndex)", family)
        self.assertIn('lua_setfield(state, -2, "__index")', family)
        self.assertIn('std::strcmp(key, "Language") == 0', family)
        self.assertNotIn('lua_setfield(state, -2, "Language")', family)

    def test_every_registered_lua_name_is_in_the_catalog(self):
        known = catalog_api_names() | catalog_owner_labels()
        missing = sorted((registered_function_names() | registered_global_names()) - known)
        self.assertEqual(missing, [], f"以下 API 已在 lua_runtime.cpp 注册但不在 Catalog：{missing}")

    def test_catalog_has_no_phantom_apis(self):
        registered = registered_function_names() | registered_global_names()
        # `Options` 的两个字段是值型条目：没有 `lua_CFunction`，绑定表里也不该有绑定行。
        # 它们的注册由 `test_options_fields_are_registered_as_values` 覆盖。
        value_only = {"HUDOffset", "Language"}
        phantom = sorted(catalog_api_names() - registered - value_only)
        self.assertEqual(phantom, [], f"Catalog 中存在运行时不注册的 API：{phantom}")

    def test_catalog_behaviour_on_host(self):
        compiler = host_compiler()
        if compiler is None:
            self.skipTest("需要宿主 C++ 编译器")
        with tempfile.TemporaryDirectory(prefix="runtime-api-catalog-") as temporary:
            directory = Path(temporary)
            source = directory / "catalog.cpp"
            binary = directory / "catalog"
            source.write_text(DRIVER, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    compiler,
                    "-std=c++17",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-I",
                    str(SRC),
                    str(source),
                    str(SRC / "interfaces" / "lua" / "api_catalog.cpp"),
                    "-o",
                    str(binary),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run_result = subprocess.run([str(binary)], capture_output=True, text=True)
            self.assertEqual(run_result.returncode, 0, run_result.stdout + run_result.stderr)
            self.assertIn("CATALOG_CHECKS_PASSED", run_result.stdout)


if __name__ == "__main__":
    unittest.main()
