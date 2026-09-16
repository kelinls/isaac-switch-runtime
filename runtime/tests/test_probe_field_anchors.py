"""`tools/probe_field_anchors.py` 的行为门禁 —— 声明式两通道探针。

**为什么需要它**：这个工具是"字段缺口"那类批次的**验收入口**。它自己坏了（解析不到 A 通道、
链走错一层、宽度没截断），报告会**看起来正常但全是错的** —— 而项目里"读数工具骗人"已经付出过
三次代价（实体地址读成槽地址、十六进制/十进制歧义、映射表截断，见交接文档的教训清单）。
所以这里把它的纯函数层逐条钉住，并附一条**合成读数的端到端重放**（不连设备）。

钉住的都是踩过的坑：

1. `g_LastLuaErrorText` 是 **256 字节且不清零** ⇒ 只认最后一个 `[tag]` 的那一行；
2. `x/1gx` 读 4 字节变量会把**相邻变量**一起读进来 ⇒ 必须按宽度截断；
3. 有符号 / 无符号：`AchievementID` 的 `-1` 是"默认可解锁"，读成无符号就永远不相等；
4. 链的每一步都要能"走断了给 0"，而不是拿半截地址去读（会读出垃圾当真值）；
5. 取证搜索要能证明"值只落在声称的那个偏移上"。
"""

import importlib.util
import json
import pathlib
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "probe_field_anchors.py"
SHIPPED_SPEC = ROOT / "tools" / "probe_specs" / "fields2.json"


def load_tool():
    spec = importlib.util.spec_from_file_location("probe_field_anchors", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeFieldAnchorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    # ---------------------------------------------------------------- A 通道解析
    def test_lua_line_uses_the_last_tag_and_trims_the_stale_tail(self):
        """缓冲区不清零 ⇒ 新错误更短时尾巴上留着上一次的文本，只认最后一个 `[fx]` 行。"""
        text = ("[b13] 旧探针的残留文本 …很长很长\n"
                "[fx] 1q=0x1 1cq=0x3 1aid=-0x1 1tags=0x108\n0x20F013A7s/modconfig.lua'…")
        fields = self.tool.parse_lua_fields(text, "fx")
        self.assertEqual(fields["1q"], "0x1")
        self.assertEqual(fields["1tags"], "0x108")
        self.assertNotIn("0x20F013A7s", " ".join(fields.values()), "尾巴必须被切掉")

    def test_lua_line_ignores_other_tags(self):
        self.assertEqual(self.tool.parse_lua_fields("[b13] idx=84\n[fx] 1q=0x1\n", "fx"),
                         {"1q": "0x1"})
        self.assertEqual(self.tool.parse_lua_fields("[b13] idx=84\n", "fx"), {})
        self.assertEqual(self.tool.parse_lua_fields("", "fx"), {})

    def test_a_value_with_leftover_text_glued_on_still_parses(self):
        """缓冲区不清零 ⇒ 值的尾巴可能粘着上一次错误的文本。

        2026-09-16 真机实测：探针报的最后一项是 `2ct=0x0`，紧接着就是上次残留的
        `o file 'rom:…modconfig.lua'…` ⇒ 取到的 token 是 `0x0o`。不切掉尾巴的话，
        这一项会被判成"A 通道没报"（看起来像探针没跑，其实是解析器太老实）。
        """
        self.assertEqual(self.tool.parse_number("0x0o"), 0)
        self.assertEqual(self.tool.parse_number("-0x1vegames"), -1)
        self.assertEqual(self.tool.parse_number("0x108\r"), 264)
        fields = self.tool.parse_lua_fields(
            "[fx] 1q=0x3 2ct=0x0o file 'rom:/isaac_mods/mods/x/scripts/modconfig.lua'", "fx")
        self.assertEqual(self.tool.parse_number(fields["2ct"]), 0)

    def test_numbers_need_a_hex_prefix_to_be_hex(self):
        """`%X` 打出来的十六进制没有前缀，看着像十进制 ⇒ 规格与脚本一律带 `0x`。"""
        self.assertEqual(self.tool.parse_number("0x108"), 264)
        self.assertEqual(self.tool.parse_number("-0x1"), -1)
        self.assertEqual(self.tool.parse_number("4242"), 4242)
        self.assertEqual(self.tool.parse_number("true"), 1)
        self.assertIsNone(self.tool.parse_number("nope"))

    # ---------------------------------------------------------------- 宽度与符号
    def test_four_byte_reads_are_truncated_and_sign_extended(self):
        """`x/1gx` 会把相邻变量一起读进来（实测把 FailureDetail 读成 0x3_0000_0005）。"""
        word = 0x0000_0001_FFFF_FFFF          # 低 4 字节 = -1（有符号），高 4 字节是邻居
        self.assertEqual(self.tool.truncate_width(word, 4, signed=True), -1,
                         "有符号字段必须按 low 32 位符号扩展，不能被邻居的高位污染")
        self.assertEqual(self.tool.truncate_width(word, 4, signed=False), 0xFFFFFFFF)
        self.assertEqual(self.tool.truncate_width(0x108, 4, signed=False), 264)
        self.assertEqual(self.tool.truncate_width(0xFF00, 1, signed=False), 0)

    # ---------------------------------------------------------------- 链求值
    def test_chain_follows_the_documented_path_and_stops_on_a_dead_read(self):
        """链 = 槽里存的是**变量地址** → 解一层才是对象 → 加偏移 → 读向量 → 取元素。"""
        memory = {0x1000: 0x2000, 0x2000: 0x3000, 0x3000 + 0x10: 0x4000}
        reader = lambda address: memory.get(address, 0)          # noqa: E731
        steps = [["slot_ptr", "0x0"], ["deref_ptr"], ["add", "0x10"], ["load_ptr", "0x0"]]
        self.assertEqual(self.tool.resolve_chain(reader, 0x1000, steps), 0x4000)
        # 向量元素
        memory[0x4000] = 0x5000
        self.assertEqual(self.tool.resolve_chain(reader, 0x1000, steps + [["vector_elem", 0]]),
                         0x5000)
        # 任何一步读不到 ⇒ 0（不许拿半截地址继续读）
        self.assertEqual(self.tool.resolve_chain(reader, 0x9999, steps), 0)
        # 链必须**以 `slot_ptr` 起头**：`current` 初值是 0，直接 `deref_ptr` 拿不到东西 ⇒ 0。
        self.assertEqual(self.tool.resolve_chain(reader, 0x1000, [["deref_ptr"]]), 0,
                         "没有起点的链必须走成 0，而不是去读 0 地址")

    def test_unknown_step_kind_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError):
            self.tool.resolve_chain(lambda address: 1, 0x10, [["magic_step", "0x0"]])

    # ---------------------------------------------------------------- 比对与取证搜索
    def test_compare_reports_mismatch_and_missing_sides(self):
        targets = [{"name": "Quality", "chain": "item1", "offset": "0xC8", "lua_key": "1q"},
                   {"name": "Tags", "chain": "item1", "offset": "0x58", "lua_key": "1tags"}]
        passed, failed = self.tool.compare_rows({"1q": "0x1", "1tags": "0x108"},
                                                {"Quality": 1, "Tags": 264}, targets)
        self.assertEqual([row["name"] for row in passed], ["Quality", "Tags"])
        self.assertEqual(failed, [])
        passed, failed = self.tool.compare_rows({"1q": "0x2", "1tags": "0x108"},
                                                {"Quality": 1, "Tags": 264}, targets)
        self.assertEqual([row["name"] for row in failed], ["Quality"])
        # A 没报 / B 读不到：都要判红并说清是哪一侧
        _, failed = self.tool.compare_rows({"1tags": "0x108"}, {"Quality": 1, "Tags": 264}, targets)
        self.assertIn("A 通道没报", failed[0]["why"])
        _, failed = self.tool.compare_rows({"1q": "0x1", "1tags": "0x108"}, {"Quality": 1}, targets)
        self.assertIn("B 通道读不到", failed[0]["why"])

    def test_float_targets_are_decoded_and_compared_with_a_tolerance(self):
        """`"kind": "float"`（2026-09-16 加）：引擎里是 IEEE-754 的字段要按浮点解，不能按整数截位。

        用例直接钉三件事：① B 通道把 4 字节按 float 解（`struct.unpack`）；
        ② 两通道差在 1e-3 以内算通过（A 通道报十进制文本，逐位比没有意义）；
        ③ 差得远（这里给 4.25 vs 9.0）必须判红 —— 否则"读错对象"这类失败会被放过。
        """
        import struct as _struct
        targets = [{"name": "Damage", "chain": "player", "offset": "0x1844", "width": 4,
                    "kind": "float", "lua_key": "pd"}]
        engine = {"Damage": _struct.unpack("<f", _struct.pack("<f", 4.25))[0]}
        passed, failed = self.tool.compare_rows({"pd": "4.25"}, engine, targets)
        self.assertEqual(failed, [])
        self.assertEqual(len(passed), 1)
        # 容差之内算过（4.2504 与 4.25 差 4e-4）
        passed, failed = self.tool.compare_rows({"pd": "4.2504"}, engine, targets)
        self.assertEqual(failed, [], failed)
        # 差得远必须红
        _, failed = self.tool.compare_rows({"pd": "9.0"}, {"Damage": 4.25}, targets)
        self.assertEqual(len(failed), 1)
        self.assertIn("容差", failed[0]["why"])
        # 规格校验：float 但宽度不是 4/8 必须在碰设备之前就被拒
        bad = {"schema": "isaac-probe-spec/1", "batch": "x", "tag": "f", "anchors": ["identity"],
               "chains": {"c": {"steps": [["game_slot_ptr", "0x0"]], "dump_bytes": 8}},
               "targets": [{"name": "A", "chain": "c", "offset": "0x0", "width": 1,
                            "kind": "float", "lua_key": "a"}]}
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad-float.json"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.tool.load_spec(path)

    def test_forensic_search_locates_the_only_offset_holding_the_value(self):
        blob = bytearray(0x100)                     # 要覆盖到 +0xC8，别把夹具写小
        blob[0xC8:0xCC] = (4).to_bytes(4, "little")
        hits = self.tool.forensic_search(bytes(blob), 4, 4)
        self.assertIn(0xC8, hits)
        self.assertNotIn(0xC0, hits)

    # ---------------------------------------------------------------- 规格
    def test_shipped_spec_is_valid_and_every_target_points_at_a_real_chain(self):
        spec = self.tool.load_spec(SHIPPED_SPEC)
        chains = set(spec["chains"])
        self.assertTrue(chains)
        for target in spec["targets"]:
            self.assertIn(target["chain"], chains)
        names = [target["name"] for target in spec["targets"]]
        self.assertEqual(len(names), len(set(names)), "字段名不能重复（报告按名字对）")

    def test_broken_specs_are_rejected_before_touching_the_device(self):
        import tempfile
        base = {"schema": "isaac-probe-spec/1", "tag": "fx",
                "chains": {"c": {"steps": [["deref_ptr"]], "dump_bytes": 32}},
                "targets": [{"name": "A", "chain": "c", "offset": "0x0", "width": 4}]}
        def write(spec):
            handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(spec, handle)
            handle.close()
            return Path(handle.name)
        self.assertIsNotNone(self.tool.load_spec(write(base)))
        broken = [
            {**base, "schema": "other/1"},
            {**base, "tag": ""},
            {**base, "chains": {}},
            {**base, "chains": {"c": {"steps": [], "dump_bytes": 32}}},
            {**base, "chains": {"c": {"steps": [["deref_ptr"]]}}},
            {**base, "targets": [{"name": "A", "chain": "nope", "offset": "0x0", "width": 4}]},
            {**base, "targets": [{"name": "A", "chain": "c", "offset": "0x0", "width": 3}]},
        ]
        for spec in broken:
            with self.subTest(spec=spec):
                with self.assertRaises(ValueError):
                    self.tool.load_spec(write(spec))

    # ---------------------------------------------------------------- 端到端重放（合成读数）
    def test_replay_scores_a_synthetic_reading_without_a_device(self):
        """把一整份合成读数喂给 `report()`：一致 ⇒ 0；改一个字节 ⇒ 1。

        这条防的是"改解码/改对照逻辑之后，报告仍然看起来正常" —— 它同时钉住
        `--from-raw` 这条路（本轮靠它省了两次 attach）。
        """
        spec = self.tool.load_spec(SHIPPED_SPEC)
        spec["spec_path"] = "tools/probe_specs/fields2.json"
        dump = bytearray(320)
        dump[0xC8:0xCC] = (1).to_bytes(4, "little")        # Quality
        dump[0xCC:0xD0] = (3).to_bytes(4, "little")        # CraftingQuality
        dump[0x50:0x54] = (0xFFFFFFFF).to_bytes(4, "little")   # AchievementID = -1
        dump[0x58:0x5C] = (0x108).to_bytes(4, "little")    # Tags
        dump[0x74:0x78] = (0).to_bytes(4, "little")        # MaxCharges
        dump[0xB0:0xB4] = (0).to_bytes(4, "little")        # ChargeType
        words = [int.from_bytes(dump[i:i + 8], "little") for i in range(0, len(dump), 8)]
        reads = {"200": [1], "204": [3], "80": [0xFFFFFFFF], "88": [0x108], "116": [0], "176": [0]}
        raw = {
            "anchors_ok": True,
            "lua_error_text": "[fx] 1q=0x1 1cq=0x3 1aid=-0x1 1tags=0x108 1mc=0x0 1ct=0x0 "
                              "2q=0x0 2ct=0x0",
            "chains": {"item1": {"address": 0x1000, "reads": reads, "dump_words": words},
                       "item118": {"address": 0x2000, "reads": {"200": [0], "176": [0]},
                                   "dump_words": []}},
        }
        self.assertEqual(self.tool.report(raw, spec), 0, "一致的读数必须判绿")
        raw["chains"]["item1"]["reads"]["200"] = [2]        # 把 Quality 改坏
        self.assertEqual(self.tool.report(raw, spec), 1, "不一致必须判红")
        raw["chains"]["item1"]["reads"]["200"] = [1]
        raw["anchors_ok"] = False
        self.assertEqual(self.tool.report(raw, spec), 1, "身份锚点不符必须判红")


if __name__ == "__main__":
    unittest.main()
