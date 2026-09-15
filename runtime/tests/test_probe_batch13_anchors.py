"""`tools/probe_batch13_anchors.py` 的门禁（**不需要设备**）。

真机上一次 attach 是稀缺资源（要停游戏、要用掉调试桩的预算），所以探针的**解释部分**必须
先在宿主上钉住：地址参照物、A 通道那一行的解析、以及"A/B 两通道怎么算对得上"。

尤其要钉住两条**已经踩过的坑**：

1. **实体的字段要读"槽里的值"指向的地址**，不是槽自己的地址 —— 2026-09-16 第一版把槽地址
   当实体地址，于是 `+0x10`/`+0x18` 读到的全是数组里的下一个槽，"实体字段"整批是垃圾；
2. **"不同就该判红"**：如果比较函数对"引擎种子与 Lua 报的不一样"也放行，这次真机验收就等于
   白跑 —— 而它恰恰是发现实现错误的唯一手段。
"""

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "probe_batch13_anchors.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("probe_batch13_anchors", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GAME_BASE = 0x36B394F000
OWNER1 = 0x4D5960A448          # `g_Game` 槽内容（游戏模块内偏移 0xABB448）
OWNER2 = 0x5E38CAF000          # 再解一层
ROOM1 = 0x5E2F67C000
ROOM2 = 0x5E2F67D000
ENTITY_POOL = 0x5E2F869700     # 网格表里第一个槽的**值**（= 实体地址）


def grid_words(pointers: dict[int, int]) -> list[int]:
    grid = [0] * 169
    for index, pointer in pointers.items():
        grid[index] = pointer
    return grid


def entity_words(variant: int, gtype: int, seed: int) -> list[int]:
    blob = bytearray(0x48)
    blob[0x10:0x14] = variant.to_bytes(4, "little")
    blob[0x18:0x1C] = gtype.to_bytes(4, "little")
    blob[0x30:0x34] = seed.to_bytes(4, "little")
    blob[0x34:0x38] = (35).to_bytes(4, "little")
    return [int.from_bytes(blob[i:i + 8], "little") for i in range(0, 0x48, 8)]


def pill_words(bits: str) -> list[int]:
    blob = bytearray(15)
    for position, flag in enumerate(bits):
        blob[position] = 1 if flag == "1" else 0
    return [int.from_bytes(blob[0:8], "little"), int.from_bytes(blob[8:16], "little")]


def raw_fixture(*, chain1=(1, 0, 71), chain2=(1, 1, 84), pills="000100000000000",
                entities=((5, 1000, 7, 0x12345678),), lua_line=None,
                two_chains=True) -> dict:
    """造一份"已经落盘"的读数（形状与真机 JSON 完全一致，能被 `--from-raw` 吃）。

    默认摆成真机 2026-09-16 那次的形态：两条链读出来的**不是同一个对象**
    （链 1 的房间索引 71、链 2 是 84），而 A 通道（运行时）报 84 ⇒ 真链应当是链 2。
    """
    words: dict[str, list[int]] = {
        "game_slot": [OWNER1],
        "c1_owner_probe": [OWNER2],
        "c1_owner": [OWNER1],
        "c2_owner": [OWNER2],
        "c1_head": [chain1[0] | (chain1[1] << 32)],
        "c1_current": [ROOM1, chain1[2]],
        "c1_pill_bits": pill_words(pills),
    }
    if two_chains:
        words["c2_head"] = [chain2[0] | (chain2[1] << 32)]
        words["c2_current"] = [ROOM2, chain2[2]]
        words["c2_pill_bits"] = pill_words(pills)
    grid = grid_words({index: ENTITY_POOL + index * 0x680 for index, _, _, _ in entities})
    words["c1_grid"] = grid
    if two_chains:
        words["c2_grid"] = grid
    for index, variant, gtype, seed in entities:
        words[f"c1_entity{index}"] = entity_words(variant, gtype, seed)
        if two_chains:
            words[f"c2_entity{index}"] = entity_words(variant, gtype, seed)
    if lua_line is None:
        #: v2 探针的短键名格式（为了塞进运行时那 256 字节的错误文本上限）
        alt = "1" if chain2[1] != 0 else "0"
        pre = "1" if (chain2[0] == 9 and (chain2[1] & ~1) == 4) else "0"
        pairs = ["i=84", f"s={chain2[0]}", f"t={chain2[1]}", f"a={alt}", f"p={pre}",
                 f"P={pills}", "X=1", "O=0", "N=0", "R=4242"]
        for position, (index, variant, gtype, seed) in enumerate(entities):
            advanced = (seed * 7 + 1) & 0xFFFFFFFF
            pairs.append(f"G{position}={index}/{variant}/0x{gtype:X}/0x{seed:X}/0x{advanced:X}")
        lua_line = "[b13] " + " ".join(pairs)
    return {
        "note": "unit",
        "base": 0x49B9981000,
        "game_base": GAME_BASE,
        "anchors_ok": True,
        "lua_error_text": lua_line,
        "raw_words": words,
    }


class ProbeBatch13AnchorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def run_report(self, raw: dict) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.tool.report(raw)
        return code, out.getvalue()

    def test_known_offsets_come_from_audited_disassembly(self):
        """偏移必须与 `runtime_constants.hpp` / 已审计反汇编一致 —— 读错一个，读数十条全废。"""
        self.assertEqual(self.tool.GAME_SLOT_OFFSET, 0xAAC698)
        self.assertEqual(self.tool.LEVEL_STAGE_OFFSET, 0x00)
        self.assertEqual(self.tool.LEVEL_STAGE_TYPE_OFFSET, 0x04)
        self.assertEqual(self.tool.CURRENT_ROOM_POINTER_OFFSET, 0x21550)
        self.assertEqual(self.tool.CURRENT_ROOM_INDEX_OFFSET, 0x21558)
        self.assertEqual(self.tool.ROOM_GRID_TABLE_OFFSET, 0x30)
        self.assertEqual(self.tool.GRID_SLOT_COUNT, 169)          # 13 × 13
        self.assertEqual(self.tool.GRID_ENTITY_VARIANT_OFFSET, 0x10)
        self.assertEqual(self.tool.GRID_ENTITY_TYPE_OFFSET, 0x18)
        self.assertEqual(self.tool.GRID_ENTITY_RNG_OFFSET, 0x30)
        self.assertEqual(self.tool.ITEM_POOL_IN_GAME_OFFSET, 0x242C0)
        self.assertEqual(self.tool.PILL_IDENTIFIED_OFFSET, 0xA68)
        self.assertEqual(self.tool.PILL_COLOR_COUNT, 15)

    def test_address_bases_are_not_confused(self):
        """★ 参照物：`g_Game` 槽在**模块映像**里，其余全在 Game/Room/ItemPool **对象**里。"""
        plan = self.tool.chain_addresses(GAME_BASE, OWNER1, {5: ENTITY_POOL}, OWNER1)
        self.assertEqual(plan["head"], (OWNER1 + 0x00, 1))
        self.assertEqual(plan["current"], (OWNER1 + 0x21550, 2))
        self.assertEqual(plan["pill_effect"], (OWNER1 + 0x242C0 + 0xA2C, 8))
        self.assertEqual(plan["pill_bits"], (OWNER1 + 0x242C0 + 0xA68, 2))

    def test_entity_fields_are_read_at_the_slot_value_not_the_slot_address(self):
        """★ 2026-09-16 踩过的坑：实体地址 = 网格表**槽里的值**，不是槽的地址。

        第一版用 `房间 + 0x30 + 下标*8` 当实体地址 ⇒ 读 `+0x10`/`+0x18` 其实读到的是数组里
        后面几个槽，整批"实体字段"都是垃圾，报告还煞有介事地打印出来。这条断言把它钉死。
        """
        plan = self.tool.chain_addresses(GAME_BASE, OWNER1, {5: ENTITY_POOL}, OWNER1)
        self.assertEqual(plan["entity5"], (ENTITY_POOL, self.tool.GRID_ENTITY_DUMP_WORDS))
        self.assertNotEqual(plan["entity5"][0], ROOM1 + 0x30 + 5 * 8)

    def test_the_rng_and_pill_tables_do_not_overlap(self):
        """药丸效果表 15×4 字节之后正好是识别位表 —— 这条算术是"读对了地方"的旁证。"""
        self.assertEqual(self.tool.PILL_IDENTIFIED_OFFSET - self.tool.PILL_EFFECT_OFFSET,
                         self.tool.PILL_COLOR_COUNT * 4)

    def test_lua_line_is_parsed_even_with_error_values(self):
        """Lua 那一行里的值可能带 `ERR:`，键是按"空格 + 键名 + =" 切的。"""
        text = ("[b13] i=84 s=9 t=4 a=0 p=0 P=000100000000000 "
                "g0=5/1000/7/305419896/2137939273/2137939273 RT=4242 "
                "X=ERR:[string \"@main.lua\"]:1: boom")
        fields = self.tool.parse_lua_report(text)
        self.assertEqual(fields["i"], "84")
        self.assertEqual(fields["g0"], "5/1000/7/305419896/2137939273/2137939273")
        self.assertEqual(fields["P"], "000100000000000")
        self.assertEqual(fields["X"], 'ERR:[string "@main.lua"]:1: boom')

    def test_trailing_garbage_from_the_previous_error_is_trimmed(self):
        """★ 运行时那个 256 字节错误缓冲**不清零**：新错误更短时，尾巴上留着上一次更长的文本。

        真机实测：`G1=1/0/0x59B57FC/0xAA17414F/0x20F013A7s/modconfig.lua'…` —— 最后一段
        粘上了旧文本。不做处理的话这一格会被判成"不是 5 段"，把一次好读数判成红。
        """
        text = ("[b13] i=71 s=1 t=1 a=1 p=0 P=000000000000000 X=1 O=0 N=0 R=4242 "
                "G0=0/0/0xCB6C783C/0xAA17414F/0x20F013A7 "
                "G1=1/0/0x59B57FC/0xAA17414F/0x20F013A7s/modconfig.lua'vegames.lua'")
        fields = self.tool.parse_lua_report(text)
        self.assertEqual(fields["G1"], "1/0/0x59B57FC/0xAA17414F/0x20F013A7")
        self.assertEqual(fields["i"], "71")
        #: `true` / `false` / `ERR:…` 不能被"数字化"清洗搞坏
        self.assertEqual(self.tool.sanitize_value("true"), "true")
        #: 尾巴上只粘了一个 `/` 时也要削掉（否则字段数多一段，那一格只能降级成"不判定"）
        self.assertEqual(self.tool.sanitize_value("0x20F013A7/"), "0x20F013A7")
        self.assertEqual(self.tool.sanitize_value("ERR:boom"), "ERR:boom")

    def test_cells_the_probe_deliberately_omits_are_not_failures(self):
        """探针只报前两格（省 256 字节），第三格读了但不该判红 —— 只作取证/说明。"""
        raw = raw_fixture(entities=((0, 0, 0xCB6C783C, 0xAA17414F),
                                    (1, 0, 0x59B57FC, 0xAA17414F),
                                    (2, 0, 0x49067754, 0xAA17414F)))
        line = raw["lua_error_text"]
        #: 只留前两格
        raw["lua_error_text"] = line.split(" G2=")[0]
        code, output = self.run_report(raw)
        self.assertEqual(code, 0, output)
        self.assertIn("只报告不判定", output)

    def test_missing_b13_line_is_not_silently_treated_as_pass(self):
        """Lua 那行没读到 ⇒ 既不算通过也不算失败（返回 3），并说明原因。"""
        code, output = self.run_report(raw_fixture(lua_line="[other] nothing to see"))
        self.assertEqual(code, 3, output)
        self.assertIn("没读到", output)

    def test_a_consistent_reading_passes(self):
        """★ 两条链不一致时，必须挑出与 A 通道一致的那条，并用它判定。"""
        code, output = self.run_report(raw_fixture())
        self.assertEqual(code, 0, output)
        self.assertIn("与 A 通道一致", output)
        self.assertIn("链 2", output)
        self.assertIn("结论：通过", output)

    def test_when_no_chain_matches_the_lua_reading_we_say_so(self):
        """A 通道与两条链都对不上 ⇒ 不下 API 的结论（返回 1），而不是硬拿一条链去比。"""
        raw = raw_fixture()
        raw["lua_error_text"] = raw["lua_error_text"].replace("i=84 s=1 t=1", "i=7 s=3 t=2")
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)
        self.assertIn("都与 A 通道不一致", output)

    def test_the_pill_true_branch_is_called_out_when_it_is_exercised(self):
        """★ 药丸那半条：只有真有颜色是"已识别"时，才算把 `true` 分支验到了。

        全 0 时两边"都是 false"也一致，但那只说明读法一致，**没有**验到"读到 1 ⇒ true"。
        这条断言把两种情形分清楚（免得报告看起来一样、结论却差一条）。
        """
        raw = raw_fixture(pills="000100000000000")
        code, output = self.run_report(raw)
        self.assertEqual(code, 0, output)
        self.assertIn("已验到 true 分支", output)
        raw = raw_fixture(pills="0" * 15)
        code, output = self.run_report(raw)
        self.assertEqual(code, 0, output)
        self.assertIn("只验到 false 分支", output)

    def test_a_wrong_variant_is_caught(self):
        """Lua 报的 variant 与引擎内存不一致必须判红（否则等于没有验收）。"""
        raw = raw_fixture()
        raw["lua_error_text"] = raw["lua_error_text"].replace("G0=5/1000/0x7/", "G0=5/1001/0x7/")
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)

    def test_a_live_reference_instead_of_a_snapshot_is_caught(self):
        """★ 快照偏离的可执行判据：Lua 侧 `Next()` 之后引擎那边的种子**必须没动**。

        这里模拟"实现成了活引用"：Lua 报的种子已经是 `Next()` 之后的值。
        """
        raw = raw_fixture()
        #: 把 G0 的"种子"换成 Next 之后的值（= 引擎那边也变成了新值 ⇒ 活引用）
        seed = 0x12345678
        advanced = (seed * 7 + 1) & 0xFFFFFFFF
        raw["lua_error_text"] = raw["lua_error_text"].replace(
            f"G0=5/1000/0x7/0x{seed:X}/0x{advanced:X}",
            f"G0=5/1000/0x7/0x{advanced:X}/0x{advanced:X}")
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)

    def test_pill_bits_are_read_byte_wise_from_the_engine(self):
        """15 位识别标志必须逐字节对照（不是只看"有没有一位是 1"）。"""
        raw = raw_fixture()
        raw["lua_error_text"] = raw["lua_error_text"].replace("P=000100000000000",
                                                             "P=000000100000000")
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)

    def test_a_truncated_lua_line_is_flagged(self):
        """错误文本顶到 256 字节上限时必须明说"后面键被截断了" —— 那是真机踩过的坑。"""
        raw = raw_fixture()
        raw["lua_error_text"] = raw["lua_error_text"].ljust(256, "x")
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)
        self.assertIn("顶到 256 字节上限", output)

    def test_stage_relations_follow_the_pc_contract(self):
        """`alt` / `pre` 由真链的关卡字段按 PC 契约推出，不照抄 Lua 的说法。"""
        chain = self.tool.chains_from_raw(raw_fixture(chain2=(9, 4, 84)))[2]
        expected = self.tool.expected_from_chain(chain)
        self.assertTrue(expected["alt"])
        self.assertTrue(expected["pre"])
        # 幕府 I（stage 9 / type 0）不是"升天前的幕府 II"
        chain = self.tool.chains_from_raw(raw_fixture(chain2=(9, 0, 84)))[2]
        expected = self.tool.expected_from_chain(chain)
        self.assertFalse(expected["alt"])
        self.assertFalse(expected["pre"])

    def test_module_scan_candidates_are_executable_only(self):
        """★ 兜底扫描只许看**可执行**段。

        要认的指纹是代码（身份函数头 16 字节），只有 Rx 段可能命中；不筛权限时一次要读上百个
        段，输出被堆/共享内存挤满、真候选被截断 —— 那一次 attach 就白烧了。
        """
        maps = [
            (0x1000, 0x9000, "r--", "Code"),
            (0x2000, 0xA000, "r-x", "Code"),
            (0x3000, 0xB000, "rw-", "Heap"),
            (0x4000, 0x5000, "r-x", "Code"),
            (0x6000, 0xE000, "r-x", "Stack"),
            (0x8000, 0x18000, "r-x", "SharedCode"),
        ]
        self.assertEqual(self.tool.DEVICE.scan_candidates(maps, 0x4000), [0x2000])

    def test_failed_identity_anchor_fails_the_whole_reading(self):
        """设备上跑的若不是本地这份构建，读数就没有意义 —— 必须判红。"""
        raw = raw_fixture()
        raw["anchors_ok"] = False
        code, output = self.run_report(raw)
        self.assertEqual(code, 1, output)
        self.assertIn("身份自校验：未通过", output)


if __name__ == "__main__":
    unittest.main()
