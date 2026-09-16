"""接口面（字段）枚举这条路的门禁 —— 三维验收之外的"第 0 维"落地部分。

**为什么需要它**：2026-09-16 之前，"缺口归零"只对**方法**成立。字段（PC 契约里的 `Variables`
节，例如 `ItemConfig_Item.Quality`）在模组源码里的写法是 `obj.Field`：
既匹配不上方法提取的正则（要求带左括号），也不在 `ApiCatalog`（目录只登记方法）
⇒ 这种"模组读了、我们没实现"的字段**从来没被问过**，症状是静默少一块功能
（诊断见 `docs/问题与解决记录.md` 2026-09-16 续七，过程教训见 `docs/错误复盘.md` 2026-09-16）。

这里钉住四件事：

1. **枚举口径本身**：载体提取必须同时认 `{"Name", offset}` 静态表与 `strcmp(x, "Name")`
   两种写法；字段读取必须同时认 `obj.F` 与 `obj["F"]` —— 少认一种就变成漏报，
   而漏报比误报危险（误报会在人工分诊时被驳掉，漏报不会被任何人看到）。
2. **账务一一对应**：没有台账条目的缺口 ⇒ 红；台账里"已实现"或"模组已不用"的条目 ⇒ 也红。
3. **口径的合成回归**：用一份**最小合成仓库**（自造清单 + 自造模组 + 自造运行时源码）
   把整套判定跑一遍，不依赖真实 EID 与真实 PC 清单 —— 真实输入变了也不会让这条口径悄悄失效。
4. **账本数字**：条数写死，变了必须有人来说明（沿用项目既有惯例）。
"""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "eid_api_gap_report.py"
LEDGER = ROOT / "tools" / "api_surface_ledger.json"

#: 台账里允许的处置。
DISPOSITIONS = ("planned", "blocked", "wontfix")

#: 账本数字（改动时必须同步在这里 +1 并写明原因，见 CONTRIBUTING.md 第二节）。
#: 2026-09-16 初版 39 条；同日 `Quality` / `CraftingQuality` 实现后删两条（37）；
#: 同日第二批 `AchievementID` / `Tags` / `MaxCharges` / `ChargeType` 实现后再删四条（33）；
#: 同日第三批（**引擎写通道**）`Sprite.Color` / `Sprite.FlipX` 两个**写**属性落地、
#: 连同 `Scale` 那条"模组自己的表"假阳性条目一起删三条（30 → 12）。剩余 12 条见台账。
LEDGER_ROWS = 12
LEDGER_PLANNED = 1
LEDGER_BLOCKED = 1
LEDGER_WONTFIX = 10


def load_tool():
    spec = importlib.util.spec_from_file_location("eid_api_gap_report", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNTHETIC_INVENTORY = """\
# 合成清单（只为门禁服务，格式与真实 `docs/PC-Lua-API-对照清单.md` 一致）

| 名字 | 种类 | 状态 | 说明 |
| --- | --- | --- | --- |
| `ItemConfigItem:Quality` | Variables | — | 属性 Quality |
| `ItemConfigItem:Tags` | Variables | — | 属性 Tags |
| `Sprite:Color` | Variables | ready | 属性 Color（已实现） |
| `RoomDescriptor:Data` | Variables | ready | 属性 Data（走布局表那条载体） |
| `ItemConfigItem:IsTrinket` | Functions | ready | 方法（走方法那条路，不该出现在字段面里） |
| `Room:GetFrameCount` | Functions | — | 链式调用目标（形态 ① `a:b():c(`） |
| `Sprite:GetAnimation` | Functions | — | 链式调用目标（形态 ② `a:b():c.d(` 里的 `d`） |
| `Room:GetType` | Functions | — | 链式调用目标（形态 ③ `(expr):c(`） |
| `Room:GetSpawnSeed` | Functions | — | 链式调用目标（形态 ② 变体 `a:b().c(`），合成 catalog 里**已实现** |
| `Sprite:GetFrame` | Functions | — | 链式调用目标，同名多 owner 里**这一家没实现** |
| `HUD:GetFrame` | Functions | — | 同上方法名的另一家，合成 catalog 里**已实现** ⇒ 只算"部分候选缺" |
| `EntityPlayer:GetPlayerType` | Functions | — | 负对照：接收者是标识符（`player:`），不属于链式那一路 |
| `Room:ModHelper` | Functions | — | 负对照：名字在 PC 方法集里，但模组自己定义过 ⇒ 必须排除 |
"""

SYNTHETIC_MOD = {
    "main.lua": "local q = item.Quality\nlocal t = item[\"Tags\"]\n"
                "local c = sprite.Color\nlocal d = room.Data\n",
}

#: 链式调用那一套的合成模组源码：三种形态 + 五条负对照。
#: 单独放一个文件，是为了**不动**上面那份 `SYNTHETIC_MOD` —— 字段面那条用例的输入保持原样。
SYNTHETIC_CHAIN_LUA = """\
-- 链式调用三种形态 + 五条负对照（只为门禁服务）
function EID:ModHelper() end
local a = game:GetRoom():GetFrameCount()        -- 形态 ①：a:b():c(
local b = holder:GetRoom():anim.GetAnimation()  -- 形态 ②：a:b():c.d(  —— c 只取成员，d 才被调用
local c = (entity):GetType()                    -- 形态 ③：(expr):c(
local d = holder:GetRoom().GetSpawnSeed()       -- 形态 ② 变体：a:b().c(
local e = sprite:GetSprite():GetFrame()         -- 形态 ①：同名多 owner（一家实现、一家没有）
local f = game:GetRoom():ModHelper()            -- 负对照 ①：模组自己定义过 ⇒ 排除
local g = player:GetPlayerType()                -- 负对照 ②：接收者是标识符 ⇒ 不属于链式那一路
local h = game:GetRoom():NotAGameApi()          -- 负对照 ③：名字不在 PC 方法集里 ⇒ 丢掉
local i = game:GetRoom().."tail"                -- 负对照 ④：`..` 是连接符，不是成员访问
local j = holder:GetRoom():anim.GetAnimation    -- 负对照 ⑤：后面没有括号 ⇒ 不是调用
"""

#: 合成 API Catalog：只登记"我们运行时确实实现了"的那几条。
#: `Room:GetFrameCount` / `Sprite:GetAnimation` / `Room:GetType` 故意**不登记** ⇒ 高置信缺口。
#: ⚠️ 这份文件里**不能**出现 `{"名字",` 或 `strcmp(x, "名字")` 这两种载体写法：
#: 字段面的载体提取会 glob 本目录下所有 `.cpp`，写进去会污染字段面那条用例的期望值。
SYNTHETIC_RUNTIME_CATALOG = """\
// 合成 catalog（只为门禁服务）
const ApiEntry kDefaultApis[] = {
    MakeId(ApiDomain::Room, 1, 0x0001), ApiDomain::Room, "Room", "GetSpawnSeed", kV1,
    MakeId(ApiDomain::Hud, 1, 0x0002), ApiDomain::Hud, "HUD", "GetFrame", kV1,
};
const ApiCatalog g_defaultCatalog{kDefaultApis};
"""


def write_synthetic_repository(root, extra_lua=None):
    """搭一份**最小合成仓库**（自造清单 + 自造目录 + 自造运行时源码 + 自造模组）。

    两条口径用例共用它：真实输入变了也不会让口径悄悄失效。
    `extra_lua` 是往模组目录里补的文件（链式那条用例用它放自己的夹具）。
    """
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "PC-Lua-API-对照清单.md").write_text(SYNTHETIC_INVENTORY, encoding="utf-8")
    cpp_dir = root / "runtime" / "src" / "interfaces" / "lua"
    cpp_dir.mkdir(parents=True)
    (cpp_dir / "synthetic.cpp").write_text(SYNTHETIC_RUNTIME_CPP, encoding="utf-8")
    (cpp_dir / "api_catalog.cpp").write_text(SYNTHETIC_RUNTIME_CATALOG, encoding="utf-8")
    (root / "tools" / "layout_tables").mkdir(parents=True)
    (root / "tools" / "layout_tables" / "synthetic.json").write_text(
        json.dumps({"rows": [{"name": "Data", "nested_in": "RoomDescriptor"}]}),
        encoding="utf-8")
    mod = root / "mod"
    mod.mkdir()
    for rel, text in SYNTHETIC_MOD.items():
        (mod / rel).write_text(text, encoding="utf-8")
    for rel, text in (extra_lua or {}).items():
        (mod / rel).write_text(text, encoding="utf-8")
    return mod

SYNTHETIC_RUNTIME_CPP = """\
// 载体写法 ①：静态字段表（`{"Name", offsetof(...)}`）
constexpr ColorField kColorFields[] = {
    {"Color", offsetof(ColorHandle, color)},
};
// 载体写法 ②：名字分派（`strcmp(<表达式>, "Name")`）
bool PushItemField(lua_State* state, std::uintptr_t item, const char* field) {
    if (std::strcmp(field, "Quality") == 0) { return true; }
    return false;
}
"""


class ApiSurfaceLedgerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()
        cls.ledger = json.loads(LEDGER.read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- 台账本身
    def test_ledger_rows_are_well_formed(self):
        """每条台账都要有：合法处置、写清理由、非空 owner 列表、不重名。"""
        seen = set()
        for row in self.ledger["rows"]:
            with self.subTest(row=row.get("name")):
                name = row["name"]
                self.assertTrue(name and name.isidentifier(), f"字段名不合法：{name!r}")
                self.assertNotIn(name, seen, f"台账里重复登记了 {name}")
                seen.add(name)
                self.assertIn(row["disposition"], DISPOSITIONS,
                              f"{name} 的处置 {row['disposition']!r} 不在 {DISPOSITIONS}")
                # 理由必须写实：只说"待定/稍后"这种不算理由（沿用偏离台账的门禁口径）。
                self.assertGreaterEqual(len(row["why"]), 12, f"{name} 的理由太短，说不清为什么")
                self.assertIsInstance(row["owners"], list)
                self.assertTrue(row["owners"], f"{name} 没写候选归属")
        self.assertEqual(self.ledger["schema"], "isaac-api-surface-ledger/1")

    def test_ledger_names_exist_in_the_pc_contract_variables(self):
        """台账里只允许登记"PC 契约确实有的字段"，防止塞进来一个不存在的名字。"""
        variables = self.tool.load_variable_owners()
        for row in self.ledger["rows"]:
            with self.subTest(name=row["name"]):
                self.assertIn(row["name"], variables,
                              f"{row['name']} 不在 PC 契约的变量节里，台账条目可疑")

    def test_the_ledger_numbers_are_pinned(self):
        """条数写死：新增/删除条目必须有人来解释（项目既有惯例）。"""
        rows = self.ledger["rows"]
        counts = {}
        for row in rows:
            counts[row["disposition"]] = counts.get(row["disposition"], 0) + 1
        self.assertEqual(len(rows), LEDGER_ROWS, f"台账条数变了：{counts}")
        self.assertEqual(counts.get("planned", 0), LEDGER_PLANNED, f"planned 变了：{counts}")
        self.assertEqual(counts.get("blocked", 0), LEDGER_BLOCKED, f"blocked 变了：{counts}")
        self.assertEqual(counts.get("wontfix", 0), LEDGER_WONTFIX, f"wontfix 变了：{counts}")

    # ---------------------------------------------------------------- 口径的合成回归
    def test_synthetic_repository_runs_the_whole_judgement(self):
        """用最小合成仓库把判定跑通 —— 载体两种写法、字段两种形态都要认出来。

        这条防的是"真实输入恰好长得像"造成的假绿：把静态表写成 `{"Name", ...}`、
        分派写成 `strcmp(x, "Name")` 是当前代码里实际存在的两种写法，
        口径必须两种都覆盖（第一版只认分派，于是 `Color.A`、`Vector.X` 被误报成缺口）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = write_synthetic_repository(root)

            original_root = self.tool.ROOT
            self.tool.ROOT = root
            try:
                report = self.tool.audit_field_surface(mod, ledger={})
            finally:
                self.tool.ROOT = original_root

        used = {row["name"] for row in report["used"]}
        self.assertEqual(used, {"Quality", "Tags", "Color", "Data"},
                         "字段读取提取漏了写法（`obj.F` 与 `obj[\"F\"]` 都要认）")
        # 三个已实现的名字分别来自三种载体：`Color`←静态表、`Quality`←名字分派、`Data`←布局表；
        # `Tags` 三种都没有 ⇒ 必须落进"未登记缺口"。
        implemented = {row["name"] for row in report["implemented"]}
        self.assertEqual(implemented, {"Quality", "Color", "Data"},
                         "载体提取漏了写法（静态表 / 名字分派 / 布局表 三者都要认）")
        unregistered = {row["name"] for row in report["unregistered"]}
        self.assertEqual(unregistered, {"Tags"},
                         "未登记缺口算错了：实现对的不该出现、没实现又没台账的必须出现")

    def test_named_constant_carrier_is_recognised(self):
        """载体写法 ③：`constexpr char kXxxField[] = "Name"` + `strcmp(field, kXxxField)`。

        这条是 2026-09-16「引擎写通道」批次补的：`Sprite` 的三个**可写**字段用命名常量分派
        （`sprite_api.cpp` 的 `SpriteNewIndex`），只认字面量的旧口径看不见它们 ⇒ 台账里
        「已完成」的条目删不掉，删了反而变成"未登记缺口"（门禁反过来红）。

        同时钉住"不许放宽"的那一半：**只声明、没被 `strcmp` 用到的常量不算载体**
        （否则随手一个字符串常量就能把缺口藏起来，那正是本文件开头说的"漏报比误报危险"）。
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cpp_dir = root / "runtime" / "src" / "interfaces" / "lua"
            cpp_dir.mkdir(parents=True)
            (cpp_dir / "synthetic.cpp").write_text(
                "constexpr char kSpriteColorField[] = \"Color\";\n"
                "constexpr char kSpriteScaleField[] = \"Scale\";\n"   # 只声明、没被用到
                "constexpr char kNotAFieldName[] = \"Whatever\";\n"   # 名字不以 Field 结尾
                "int SpriteNewIndex(lua_State* state) {\n"
                "    if (std::strcmp(field, kSpriteColorField) == 0) { return 0; }\n"
                "    if (std::strcmp(field, kNotAFieldName) == 0) { return 0; }\n"
                "    return -1;\n"
                "}\n",
                encoding="utf-8")
            original_root = self.tool.ROOT
            self.tool.ROOT = root
            try:
                implemented = self.tool.load_implemented_fields()
            finally:
                self.tool.ROOT = original_root

        self.assertIn("Color", implemented,
                      "命名常量 + strcmp 的载体没被认出来（Sprite.Color 写通道就是这么实现的）")
        self.assertNotIn("Scale", implemented,
                         "只声明、没被 strcmp 用到的常量不算载体（否则缺口会被藏起来）")
        self.assertNotIn("Whatever", implemented,
                         "常量名不以 Field 结尾的字符串常量不算载体")

    def test_ledger_covers_every_gap_and_has_no_stale_rows(self):
        """台账与缺口必须一一对应：有缺口没登记 ⇒ 红；登记了却已实现/不再用 ⇒ 也红。"""
        mod = ROOT / self.tool.DEFAULT_MOD
        if not mod.is_dir():
            self.skipTest(f"缺少模组夹具：{mod}")
        report = self.tool.audit_field_surface(mod)
        self.assertEqual(
            [row["name"] for row in report["unregistered"]], [],
            "有字段缺口没有台账条目：请逐条分诊后写进 tools/api_surface_ledger.json")
        self.assertEqual(
            [row["name"] for row in report["stale_ledger_rows"]], [],
            "台账里有过期条目（已实现或模组不再读）：删掉它们并同步账本数字")

    def test_the_pc_variable_section_is_actually_parsed(self):
        """PC 契约的变量节必须真的解析出量来（防"分节格式变了导致全空"的静默失效）。"""
        variables = self.tool.load_variable_owners()
        self.assertGreater(len(variables), 300,
                           f"只解析出 {len(variables)} 个字段名 —— 清单格式或解析规则坏了")
        self.assertIn("Quality", variables)


class ChainedCallEnumerationTests(unittest.TestCase):
    """**链式调用**枚举这条路的门禁（2026-09-16 补）。

    **为什么需要它**：老口径提取的是 `Owner:Name(` —— 要求 `:` 左边**是一个标识符**。
    模组里 `game:GetRoom():GetFrameCount()` 这种"在另一个调用的结果上再调方法"的写法，
    `:` 左边是 `)` ⇒ 老正则只看得见最内层的 `game:GetRoom`，**`GetFrameCount` 整条扫不到**
    ⇒ `Room:GetFrameCount` 这个真实缺口**从来没进过台账**，直到 2026-09-16 真机上
    EID 的渲染回调每帧抛这个错才被发现（`features/eid_bagofcrafting.lua:967`，
    玩家一持有背包合成 → 每帧 `attempt to call a nil value (method 'GetFrameCount')`
    → 整段道具描述渲染被打断；过程见 `docs/问题与解决记录.md` 2026-09-16 续十八/续十九）。

    这里钉三件事：
    1. **形态**：`a:b():c(`、`a:b():c.d(`、`(expr):c(` 三种写法都要认出来；同时
       "接收者是标识符"（`player:GetPlayerType()`）**不算链式**，它归老那一路；
    2. **不放松到"所有 `:名字(`"**：被调用的名字必须出现在 PC 侧方法名集合里，
       模组自己定义过的方法名要排除（判据见 `tools/eid_api_gap_report.py` 的"链式调用枚举"一段）；
    3. **口径是"owner 未知"**：只报方法名 + 候选 owner，绝不写死成 `Room:GetFrameCount`。
    """

    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    # ---------------------------------------------------------------- 形态
    def test_the_three_chained_forms_are_recognised(self):
        """三种链式写法都要认出"真正被调用"的那个名字（不是链里所有的成员名）。"""
        source = (
            "local a = game:GetRoom():GetFrameCount()\n"       # 形态 ①
            "local b = holder:GetRoom():anim.GetAnimation()\n"  # 形态 ②：`anim` 只取成员
            "local c = (entity):GetType()\n"                    # 形态 ③
            "local d = holder:GetRoom().GetSpawnSeed()\n"       # 形态 ② 变体
        )
        self.assertEqual(list(self.tool.iter_chained_calls(source)),
                         ["GetFrameCount", "GetAnimation", "GetType", "GetSpawnSeed"],
                         "链式调用的三种形态有漏（`a:b():c(` / `a:b():c.d(` / `(expr):c(`）")

    def test_the_old_identifier_path_cannot_see_the_chain(self):
        """反向对照：老口径对同一条源码**只看得到最内层那次调用** —— 这就是缺口进不了台账的原因。

        这条用例是"事故墓碑"：它断言的是**老口径的盲区本身**（不是它坏了）。
        哪天有人把 `load_mod_usage()` 也改成认链式，这条会红 —— 那时应当把它删掉，
        并把断言并进上一条，而不是改老正则迁就它。
        """
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp)
            (mod / "main.lua").write_text("local n = game:GetRoom():GetFrameCount()\n",
                                          encoding="utf-8")
            old_usage = self.tool.load_mod_usage(mod)
            chained = self.tool.load_mod_chained_usage(mod)

        self.assertEqual(set(old_usage), {"game:GetRoom"},
                         "老口径认的应当是 `Owner:Name(`：这里只有最内层的 `game:GetRoom`")
        self.assertNotIn("GetFrameCount", {k.split(":", 1)[1] for k in old_usage},
                         "老口径不该看见链式那一段 —— 这正是本次要修的盲区")
        self.assertIn("GetFrameCount", chained,
                      "新口径必须看得见 `game:GetRoom():GetFrameCount()` 里的链式调用")

    # ---------------------------------------------------------------- 口径的合成回归
    def test_synthetic_repository_recognises_chained_calls(self):
        """最小合成仓库端到端：三种形态收进来，五条负对照一条都不能收。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mod = write_synthetic_repository(root, {"chain.lua": SYNTHETIC_CHAIN_LUA})

            original_root = self.tool.ROOT
            self.tool.ROOT = root
            try:
                report = self.tool.audit_chained_calls(mod)
            finally:
                self.tool.ROOT = original_root

        names = {row["name"] for row in report["rows"]}
        self.assertEqual(names, {"GetFrameCount", "GetAnimation", "GetType", "GetSpawnSeed", "GetFrame"},
                         "链式枚举结果不对：漏认（三种形态）或误收（负对照）都会在这里现形")
        # `ModHelper` 在 PC 方法集里、但模组自己 `function EID:ModHelper()` 定义过 ⇒ 排除；
        # `GetPlayerType` 的接收者是标识符 ⇒ 归老那一路；`NotAGameApi` 不在 PC 方法集里 ⇒ 丢掉。
        for excluded in ("ModHelper", "GetPlayerType", "NotAGameApi"):
            with self.subTest(excluded=excluded):
                self.assertNotIn(excluded, names, f"{excluded} 不该出现在链式这一路")

        every_missing = {row["name"] for row in report["every_candidate_missing"]}
        self.assertEqual(every_missing, {"GetFrameCount", "GetAnimation", "GetType"},
                         "高置信缺口算错了：候选 owner **全部**没有的才算高置信")
        # `GetFrame` 的两家 owner 里 `HUD:GetFrame` 合成 catalog 里已实现 ⇒ 只算"部分候选缺"，
        # 不能进高置信清单（否则 `icon[1]:Update(...)` 这类会被长期误当成缺口）。
        self.assertNotIn("GetFrame", every_missing)
        self.assertIn("GetFrame", {row["name"] for row in report["with_candidate_gap"]})
        get_spawn_seed = next(row for row in report["rows"] if row["name"] == "GetSpawnSeed")
        self.assertEqual(get_spawn_seed["missing_owners"], [],
                         "`Room:GetSpawnSeed` 合成 catalog 里已经实现了，不该算缺口")

    def test_the_incident_case_is_enumerated_in_the_real_mod(self):
        """真夹具上的回归：EID 那句 `game:GetRoom():GetFrameCount()` 必须仍被枚举到。"""
        mod = ROOT / self.tool.DEFAULT_MOD
        if not mod.is_dir():
            self.skipTest(f"缺少模组夹具：{mod}")
        chained = self.tool.load_mod_chained_usage(mod)
        self.assertIn("GetFrameCount", chained,
                      "EID `features/eid_bagofcrafting.lua:967` 的链式调用又扫不到了 —— "
                      "2026-09-16 那次每帧报错的缺口就是这样溜过去的")

    def test_the_chain_filter_does_not_let_every_colon_through(self):
        """判据 ③ 必须真的在过滤：模组自己的 `t:gsub()` / `s:find()` 不能被当成游戏 API。

        这条防的是"为了好看把口径放宽成所有 `:名字(`" —— 那样每个模组都会凭空多出几百条噪音。
        """
        source = (
            "local a = someString:sub():gsub()\n"
            "local b = tbl:pack():modifierFunction()\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            mod = Path(tmp)
            (mod / "main.lua").write_text(source, encoding="utf-8")
            chained = self.tool.load_mod_chained_usage(mod)
        self.assertEqual(dict(chained), {},
                         "非游戏方法名（gsub / package / modifierFunction）不该进链式清单")


if __name__ == "__main__":
    unittest.main()
