import ast
import subprocess
import tempfile
import unittest
import hashlib
import re
from pathlib import Path

try:
    from .test_support import layered_lua_runtime_sources
except ImportError:
    from test_support import layered_lua_runtime_sources


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runtime" / "source"

# The fixed PC contract: these globals must exist in Lua, in this order, and
# `ModCallbacks` is registered last.
GENERATED_TABLES = (
    "CollectibleType",
    "ItemPoolType",
    "RoomType",
    "LevelStage",
    "PickupVariant",
    "ItemType",
    "PlayerForm",
    "PlayerType",
    "PillColor",
    "PillEffect",
    "EntityType",
    "UseFlag",
    "TrinketType",
    "Challenge",
    "LevelCurse",
    "GridEntityType",
    "EffectVariant",
    "EntityPartition",
    "Card",
    "ModCallbacks",
)

# Entry counts pinned to the enums.lua revision identified by the SHA-256 that
# the generator writes into both products.
EXPECTED_ENTRY_COUNTS = {
    "CollectibleType": 753,
    "ItemPoolType": 40,
    "RoomType": 31,
    "LevelStage": 23,
    "PickupVariant": 30,
    "ItemType": 5,
    "PlayerForm": 16,
    "PlayerType": 47,
    "PillColor": 19,
    "PillEffect": 52,
    "EntityType": 344,
    "UseFlag": 12,
    "TrinketType": 195,
    "Challenge": 47,
    "LevelCurse": 10,
    "GridEntityType": 28,
    "EffectVariant": 200,
    "EntityPartition": 7,
    "Card": 100,
    "ModCallbacks": 74,
}

# ModCallbacks values the Runtime must publish for mod registration to work.
EXPECTED_MOD_CALLBACKS = {
    "MC_POST_UPDATE": 1,
    "MC_POST_RENDER": 2,
    "MC_INPUT_ACTION": 13,
    "MC_PRE_GET_COLLECTIBLE": 62,
    "MC_POST_GET_COLLECTIBLE": 63,
    "MC_USE_CARD": 5,
    "MC_PRE_USE_ITEM": 23,
    "MC_POST_NEW_ROOM": 19,
    "MC_POST_GAME_STARTED": 15,
    "MC_POST_PICKUP_INIT": 34,
    "MC_POST_PICKUP_UPDATE": 35,
    "MC_PRE_ROOM_ENTITY_SPAWN": 71,
    "MC_PRE_PICKUP_COLLISION": 38,
    "MC_PRE_GAME_EXIT": 17,
    "MC_POST_NEW_LEVEL": 18,
    "MC_POST_PLAYER_INIT": 9,
}

K_TABLE_ENTRY = re.compile(
    r'\{"([A-Za-z0-9_]+)", k([A-Za-z0-9_]+)Values, '
    r'sizeof\(k\2Values\) / sizeof\(k\2Values\[0\]\)\},'
)
K_VALUE_ENTRY = re.compile(r'^\s*\{"([A-Z0-9_]+)", -?\d+\},$', re.MULTILINE)


def lua_table_literal(mapping):
    return "{" + ", ".join(f'["{key}"] = {value}' for key, value in mapping.items()) + "}"


def load_generator():
    """The generator module itself, loaded by path (it is a script, not a package)."""
    import importlib.util

    path = ROOT / "tools" / "generate_pc_lua_enum_tables.py"
    spec = importlib.util.spec_from_file_location("generate_pc_lua_enum_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def registered_tables(implementation):
    """Ordered (global name, value-array symbol) pairs from the generated kTables."""
    return K_TABLE_ENTRY.findall(implementation)


def generated_keys(implementation, symbol):
    """Keys of one generated value array, in generated (sorted) order."""
    match = re.search(
        rf"constexpr Value k{symbol}Values\[\] = \{{(.*?)\n\}};", implementation, re.DOTALL
    )
    if match is None:
        return None
    return K_VALUE_ENTRY.findall(match.group(1))


class LuaGlobalPcEnumTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="isaac-lua-global-enums-")
        temporary = Path(cls.temporary.name)
        compatibility = temporary / "compatibility"
        compatibility.mkdir()
        (compatibility / "stdfloat").write_text(
            "#pragma once\nnamespace std { using float16_t = float; using float128_t = long double; }\n"
        )
        harness = temporary / "lua_global_enums_harness.cpp"
        harness.write_text(
            r'''
#include "lua_runtime.hpp"
#include "game_file_reader.hpp"
#include "game_observer.hpp"

#include <cstring>

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

int main() {
    const char* script = R"lua(
local function CheckGlobalPcEnumTables()
local expectedCounts = __EXPECTED_COUNTS__
local expectedModCallbacks = __EXPECTED_MOD_CALLBACKS__
local missing = {}
for name, count in pairs(expectedCounts) do
    local published = _G[name]
    if type(published) ~= 'table' then
        missing[#missing + 1] = name
    else
        local actual = 0
        for _ in pairs(published) do actual = actual + 1 end
        if actual ~= count then
            error(name .. ' entry count ' .. actual .. ' ~= ' .. count)
        end
    end
end
if #missing > 0 then error('missing global tables: ' .. table.concat(missing, ',')) end
for name, value in pairs(expectedModCallbacks) do
    if ModCallbacks[name] ~= value then
        error('ModCallbacks.' .. name .. ' = ' .. tostring(ModCallbacks[name]) .. ' ~= ' .. value)
    end
end
if CollectibleType.COLLECTIBLE_DEATH_CERTIFICATE ~= 628 then error('collectible enum') end
if CollectibleType.NUM_COLLECTIBLES ~= 733 then error('collectible count') end
if ItemPoolType.POOL_ULTRA_SECRET ~= 24 then error('item pool enum') end
if RoomType.ROOM_ULTRASECRET ~= 29 then error('room enum') end
if LevelStage.STAGE8 ~= 13 then error('level enum') end
if PickupVariant.PICKUP_COLLECTIBLE ~= 100 then error('pickup variant enum') end
if ItemType.ITEM_ACTIVE ~= 3 then error('item type enum') end
if PlayerForm.PLAYERFORM_GUPPY ~= 0 then error('player form enum') end
if PlayerType.PLAYER_ISAAC ~= 0 then error('player type enum') end
if PillColor.PILL_NULL ~= 0 then error('pill color enum') end
if PillEffect.PILLEFFECT_BAD_GAS ~= 0 then error('pill effect enum') end
if EntityType.ENTITY_PLAYER ~= 1 then error('entity type enum') end
if UseFlag.USE_OWNED ~= 4 then error('use flag enum') end
if TrinketType.TRINKET_NULL ~= 0 then error('trinket enum') end
if Challenge.CHALLENGE_NULL ~= 0 then error('challenge enum') end
if LevelCurse.CURSE_OF_THE_UNKNOWN ~= 8 then error('curse enum') end
if GridEntityType.GRID_ROCK ~= 2 then error('grid entity enum') end
if EffectVariant.EFFECT_NULL ~= 0 then error('effect variant enum') end
if EntityPartition.PLAYER ~= 32 then error('entity partition enum') end
if Card.CARD_FOOL ~= 1 then error('card enum') end
-- Bit-flag tables: `enums.lua` writes every one of these as a shift expression
-- (`1<<5`, `1 << 5`), and the generator used to truncate each one to its leading
-- `1`. Every member is pinned here so that regression cannot come back silently.
local entityPartition = {
    ['FAMILIAR'] = 1, ['BULLET'] = 2, ['TEAR'] = 4, ['ENEMY'] = 8,
    ['PICKUP'] = 16, ['PLAYER'] = 32, ['EFFECT'] = 64,
}
local partitionCount = 0
for name, value in pairs(entityPartition) do
    partitionCount = partitionCount + 1
    if EntityPartition[name] ~= value then
        error('EntityPartition.' .. name .. ' = ' .. tostring(EntityPartition[name]) ..
              ' ~= ' .. value)
    end
end
for name, _ in pairs(EntityPartition) do
    if entityPartition[name] == nil then error('unexpected EntityPartition.' .. name) end
end
if partitionCount ~= 7 then error('EntityPartition member count') end
-- EID 的 `searchPartitions`（`main.lua:1304`）就是这四个分区的和；它曾经算成 4
-- （等于 TEAR），正确的掩码是 57。
if EntityPartition.FAMILIAR + EntityPartition.ENEMY + EntityPartition.PICKUP +
   EntityPartition.PLAYER ~= 57 then
    error('EID searchPartitions mask')
end
if UseFlag.USE_NOANIM ~= 1 or UseFlag.USE_NOHUD ~= 2048 then error('UseFlag bits') end
if LevelCurse.CURSE_OF_DARKNESS ~= 1 or LevelCurse.CURSE_OF_GIANT ~= 128 then
    error('LevelCurse bits')
end
-- 十六进制字面量同样曾经被截断成 0（`0x800` → `0`）。
if PillColor.PILL_GIANT_FLAG ~= 2048 or PillColor.PILL_COLOR_MASK ~= 2047 then
    error('PillColor hex bits')
end
if TrinketType.TRINKET_GOLDEN_FLAG ~= 32768 or TrinketType.TRINKET_ID_MASK ~= 32767 then
    error('TrinketType hex bits')
end
local mod = RegisterMod('Global enum probe', 1)
mod:AddCallback(ModCallbacks.MC_POST_UPDATE, function() end)
end
-- The runtime returns a bare failure code, so report which check failed on
-- stdout before failing; the test surfaces that text in its own message.
local ok, failure = pcall(CheckGlobalPcEnumTables)
if not ok then
    print('global PC enum contract check failed: ' .. tostring(failure))
    error(failure)
end
)lua";
    return LuaRuntime::InitializeFromBuffer(script, std::strlen(script), "@global-enums.lua") ==
        LuaRuntime::LuaInitResult::Success ? 0 : 1;
}
'''.lstrip()
            .replace("__EXPECTED_COUNTS__", lua_table_literal(EXPECTED_ENTRY_COUNTS))
            .replace("__EXPECTED_MOD_CALLBACKS__", lua_table_literal(EXPECTED_MOD_CALLBACKS))
        )
        lua_root = SOURCE / "third_party/lua-5.3.3/src"
        excluded = {"lua.c", "luac.c", "liolib.c", "loslib.c", "loadlib.c", "ldblib.c", "linit.c"}
        lua_objects = []
        for lua_source in sorted(lua_root.glob("*.c")):
            if lua_source.name in excluded:
                continue
            output = temporary / f"{lua_source.stem}.o"
            build = subprocess.run(
                ["cc", "-std=c99", "-w", "-DLUA_C89_NUMBERS", "-I", str(lua_root), "-c",
                 str(lua_source), "-o", str(output)], text=True, capture_output=True,
            )
            if build.returncode != 0:
                raise AssertionError(build.stdout + build.stderr)
            lua_objects.append(output)
        cls.harness = temporary / "lua_global_enums_harness"
        # `lua_runtime.cpp` textually includes the generated implementation, so
        # linking it again would define PcLuaEnumData::Tables twice. Only pass the
        # generated file as its own translation unit when the runtime no longer
        # includes it; either way it is compiled exactly once.
        runtime_include = '#include "program/pc_lua_enum_data.cpp"'
        generated_sources = (
            [] if runtime_include in (SOURCE / "lua_runtime.cpp").read_text()
            else [str(SOURCE / "program/pc_lua_enum_data.cpp")]
        )
        build = subprocess.run(
            ["c++", "-std=c++23", "-Wall", "-Wextra", "-Werror", "-DLUA_C89_NUMBERS",
             "-DEXL_LAYERED_RUNTIME=1", "-DEXL_LOAD_KIND=Module",
             "-DEXL_LOAD_KIND_ENUM=2", "-DEXL_PROGRAM_ID=0",
             "-I", str(compatibility), "-I", str(SOURCE), "-I", str(SOURCE.parent / "src"),
             "-I", str(lua_root), str(harness),
             *(str(path) for path in layered_lua_runtime_sources(SOURCE)),
             *generated_sources, *map(str, lua_objects),
             "-o", str(cls.harness)], text=True, capture_output=True,
        )
        if build.returncode != 0:
            raise AssertionError(build.stdout + build.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_global_pc_enum_tables_match_the_fixed_pc_contract(self):
        result = subprocess.run([str(self.harness)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_generated_tables_are_global_and_not_starterr_specific(self):
        enum_source = ROOT / "The Binding of Isaac Rebirth pc" / "resources/scripts/enums.lua"
        header = SOURCE / "program/pc_lua_enum_data.hpp"
        implementation = SOURCE / "program/pc_lua_enum_data.cpp"
        digest = hashlib.sha256(enum_source.read_bytes()).hexdigest()
        self.assertIn(f"enums.lua SHA-256: {digest}", header.read_text())
        generated = implementation.read_text()
        self.assertIn(f"enums.lua SHA-256: {digest}", generated)

        tables = registered_tables(generated)
        self.assertEqual(tuple(name for name, _ in tables), GENERATED_TABLES)
        self.assertEqual(tables[-1][0], "ModCallbacks")

        counted = {}
        for name, symbol in tables:
            keys = generated_keys(generated, symbol)
            self.assertIsNotNone(keys, f"missing generated value array for {name}: k{symbol}Values")
            self.assertEqual(len(keys), len(set(keys)), f"duplicate keys generated for {name}")
            counted[name] = len(keys)
        self.assertEqual(counted, EXPECTED_ENTRY_COUNTS)

        self.assertNotIn("Starterr", generated)
        self.assertNotIn("starterr", generated)
        self.assertEqual(len(re.findall(r'\{"COLLECTIBLE_[A-Z0-9_]+",', generated)), 752)
        self.assertIn('{"NUM_COLLECTIBLES", 733}', generated)
        self.assertEqual(len(re.findall(r'\{"POOL_[A-Z0-9_]+",', generated)), 39)
        self.assertEqual(len(re.findall(r'\{"ROOM_[A-Z0-9_]+",', generated)), 30)
        self.assertEqual(len(re.findall(r'\{"STAGE[A-Z0-9_]+",', generated)), 21)
        self.assertIn('{"NUM_ITEMPOOLS", 31}', generated)
        self.assertIn('{"NUM_ROOMTYPES", 30}', generated)
        self.assertIn('{"NUM_STAGES", 14}', generated)
        self.assertIn('{"MC_POST_UPDATE", 1}', generated)
        self.assertIn('{"MC_PRE_GET_COLLECTIBLE", 62}', generated)
        self.assertIn('{"MC_POST_GET_COLLECTIBLE", 63}', generated)
        self.assertIn('{"MC_PRE_ROOM_ENTITY_SPAWN", 71}', generated)
        self.assertIn('{"ENTITY_PLAYER", 1}', generated)
        self.assertIn('{"USE_OWNED", 4}', generated)
        # 位标志表（`1<<N` / `1 << N`）与十六进制字面量都必须按源码取值生成。
        self.assertIn('{"FAMILIAR", 1}', generated)
        self.assertIn('{"BULLET", 2}', generated)
        self.assertIn('{"TEAR", 4}', generated)
        self.assertIn('{"ENEMY", 8}', generated)
        self.assertIn('{"PICKUP", 16}', generated)
        self.assertIn('{"PLAYER", 32}', generated)
        self.assertIn('{"EFFECT", 64}', generated)
        self.assertIn('{"USE_NOHUD", 2048}', generated)
        self.assertIn('{"CURSE_OF_THE_UNKNOWN", 8}', generated)
        self.assertIn('{"CURSE_OF_GIANT", 128}', generated)
        self.assertIn('{"PILL_COLOR_MASK", 2047}', generated)
        self.assertIn('{"PILL_GIANT_FLAG", 2048}', generated)
        self.assertIn('{"TRINKET_ID_MASK", 32767}', generated)
        self.assertIn('{"TRINKET_GOLDEN_FLAG", 32768}', generated)
        # 截断产物（位移表达式只剩开头的 `1`、`0x...` 只剩 `0`）必须彻底消失。
        self.assertNotIn('{"BULLET", 1}', generated)
        self.assertNotIn('{"TEAR", 1}', generated)
        self.assertNotIn('{"ENEMY", 1}', generated)
        self.assertNotIn('{"PICKUP", 1}', generated)
        self.assertNotIn('{"PLAYER", 1}', generated)
        self.assertNotIn('{"EFFECT", 1}', generated)
        self.assertNotIn('{"PILL_COLOR_MASK", 0}', generated)
        self.assertNotIn('{"TRINKET_ID_MASK", 0}', generated)
        for name, symbol in tables:
            self.assertIn(
                f'{{"{name}", k{symbol}Values, '
                f'sizeof(k{symbol}Values) / sizeof(k{symbol}Values[0])}},',
                generated,
            )

    def test_generator_contract_has_no_mod_name_or_source_dependency(self):
        generator = (ROOT / "tools/generate_pc_lua_enum_tables.py").read_text()
        self.assertIn("--enum-source", generator)
        self.assertNotIn("Starterr", generator)
        self.assertNotIn("starterr", generator)
        tables = None
        for node in ast.parse(generator).body:
            if isinstance(node, ast.Assign) and any(
                getattr(target, "id", None) == "TABLES" for target in node.targets
            ):
                tables = ast.literal_eval(node.value)
        self.assertIsNotNone(tables, "generator TABLES tuple not found")
        self.assertEqual(tuple(tables), GENERATED_TABLES)
        self.assertEqual(tuple(tables)[0], "CollectibleType")
        self.assertEqual(tuple(tables)[-1], "ModCallbacks")

    # --- 生成器取值：位移/十六进制回归 ------------------------------------------
    #
    # 这一组用例**直接对着生成器**（不等价于产物断言）：旧的条目正则只认十进制字面量，
    # 于是 `1<<3` 被读成 `1`、`0x800` 被读成 `0`，而"没匹配上"的行还会被静默跳过。
    # 这里同时钉住"算得对"和"读不懂就报错"两件事。

    def test_generator_evaluates_shift_and_hex_value_expressions(self):
        generator = load_generator()
        evaluate = generator._evaluate_expression
        cases = {
            "1 << 3": 8,
            "1<<3": 8,
            "(1 << 3)": 8,
            "1 << 1": 2,
            "1 << 30": 0x40000000,
            "3 << 2": 12,
            "0x800": 2048,
            "0X7ff": 2047,
            "0x8000": 32768,
            "0": 0,
            "-1": -1,
            "1 | 4": 5,
            "1 + 4": 5,
            "(1 << 2) | 1": 5,
        }
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression, "Table", "KEY"), expected)

    def test_generator_rejects_value_expressions_it_cannot_evaluate(self):
        generator = load_generator()
        evaluate = generator._evaluate_expression
        # 每一个都是"旧实现会静默截断或静默跳过"的形状，现在必须显式失败。
        for expression in ("TEARFLAG(1)", "BitSet128(0, 0)", "1 << 3 4", "OTHER_KEY + 1",
                           "1.5", "1 << ", ""):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    evaluate(expression, "Table", "KEY")
        # 生成的表把值存成 `std::int32_t`，所以超出 int32 的表达式必须**报错**而不是回绕。
        # 这条闸门同时说明了为什么 `EntityFlag`/`ProjectileFlags`/`DamageFlag`（`1<<32`、
        # `0x80000000` 一类）今天还不能进 `TABLES`：要发布它们得先把存储拓宽到 64 位。
        for expression in ("1 << 31", "1 << 32", "0x80000000"):
            with self.subTest(expression=expression):
                with self.assertRaises(ValueError):
                    evaluate(expression, "Table", "KEY")

    def test_generator_fails_loudly_on_an_unparsable_table_entry(self):
        generator = load_generator()
        with self.assertRaises(ValueError):
            generator._parse_table("Leftover = {\n    A = TEARFLAG(1),\n}", "Leftover")
        # 能读的行照旧读出来（同一个函数里两种行为并存，不是"全都报错"）。
        parsed = generator._parse_table(
            "Leftover = {\n    A = 1 << 3, -- comment\n    B = A,\n}", "Leftover"
        )
        self.assertEqual(parsed, {"A": 8, "B": 8})

    def test_generator_reads_the_frozen_source_bit_flag_tables(self):
        generator = load_generator()
        enum_source = ROOT / "The Binding of Isaac Rebirth pc" / "resources/scripts/enums.lua"
        source = enum_source.read_text(encoding="utf-8")
        values = generator._parse_values(source)
        self.assertEqual(
            values["EntityPartition"],
            {"FAMILIAR": 1, "BULLET": 2, "TEAR": 4, "ENEMY": 8, "PICKUP": 16,
             "PLAYER": 32, "EFFECT": 64},
        )
        self.assertEqual(values["UseFlag"]["USE_NOCOSTUME"], 2)
        self.assertEqual(values["UseFlag"]["USE_OWNED"], 4)
        self.assertEqual(values["UseFlag"]["USE_NOHUD"], 2048)
        self.assertEqual(values["LevelCurse"]["CURSE_OF_THE_UNKNOWN"], 8)
        self.assertEqual(values["LevelCurse"]["CURSE_OF_GIANT"], 128)
        self.assertEqual(values["LevelCurse"]["NUM_CURSES"], 9)
        self.assertEqual(values["PillColor"]["PILL_GIANT_FLAG"], 0x800)
        self.assertEqual(values["PillColor"]["PILL_COLOR_MASK"], 0x7FF)
        self.assertEqual(values["TrinketType"]["TRINKET_GOLDEN_FLAG"], 0x8000)
        self.assertEqual(values["TrinketType"]["TRINKET_ID_MASK"], 0x7FFF)

        # 独立复核：源码里每一个 `1 << N` 形状的条目都必须等于 `1 << N`
        # （用正则自己算一遍，不复用生成器的求值器，避免同义反复）。
        shift_entries = 0
        for table in GENERATED_TABLES:
            body = generator._table_body(source, table)
            for line in body.splitlines():
                match = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*1\s*<<\s*(\d+)\s*,?\s*(?:--.*)?$", line)
                if match is None:
                    continue
                shift_entries += 1
                self.assertEqual(
                    values[table][match.group(1)], 1 << int(match.group(2)),
                    f"{table}.{match.group(1)}",
                )
        # 这个数字就是"曾经全部被截断成 1"的条目数，掉了说明用例自己失效了。
        self.assertEqual(shift_entries, 24)


if __name__ == "__main__":
    unittest.main()
