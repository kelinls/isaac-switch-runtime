"""`tools/probe_room_descriptor.py` 的门禁（**不需要设备**）。

探针要连真机才能取数，但它的"解释部分"完全可以在宿主上验：偏移常量、容器解释、
步长推算、报告渲染、离线重放。这几条一旦错了，真机读数会被解释成错误结论 ——
而一次真机会话是有成本的（要停游戏、要占调试桩的 attach 预算），不能拿它当调试手段。
"""

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "probe_room_descriptor.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("probe_room_descriptor", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProbeRoomDescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_tool()

    def test_known_offsets_come_from_audited_disassembly(self):
        """偏移常量必须与已审计的反汇编一致（`g_Game` 槽、容器、当前房间三件套）。

        这些值不是"大概对"就行：读错一个偏移，探针会把别的字段当成候选，
        于是真机读数看起来有理有据、结论却是错的。值都在 `docs/问题与解决记录.md`
        与布局表里有出处。
        """
        self.assertEqual(self.tool.GAME_SLOT_OFFSET, 0xAAC698)
        self.assertEqual(self.tool.DESCRIPTOR_CONTAINER_OFFSET, 0x21510)
        self.assertEqual(self.tool.CURRENT_ROOM_POINTER_OFFSET, 0x21550)
        self.assertEqual(self.tool.CURRENT_ROOM_INDEX_OFFSET, 0x21558)
        self.assertEqual(self.tool.CURRENT_DIMENSION_OFFSET, 0x21560)
        # 容器内读 `+0x21560` ⇒ 相对容器起点就是 0x50；这条关系错了会取到别的字段。
        self.assertEqual(self.tool.CURRENT_DIMENSION_OFFSET - self.tool.DESCRIPTOR_CONTAINER_OFFSET,
                         0x50)

    def test_address_bases_are_not_confused(self):
        """回归：容器与"当前房间三件套"在 **Game 对象**里，不在模块映像里。

        第一版把这两个读地址也算成了 `模块基址 + 偏移`，于是真机读到的是**代码字节**
        —— 打印出来"有值"，实际全是垃圾，白烧一次 attach。这条断言把参照物钉死：
        `g_Game` 槽相对模块基址，容器/当前房间相对 Game 指针。
        """
        game_base, game = 0x10000000, 0x20000000
        addresses = self.tool.plan_addresses(game_base, game)
        self.assertEqual(addresses["game_slot"], game_base + 0xAAC698)
        self.assertEqual(addresses["container"], game + 0x21510)
        self.assertEqual(addresses["current"], game + 0x21550)
        # 明确不许出现"容器/当前房间相对模块基址"的写法
        self.assertNotEqual(addresses["container"], game_base + 0x21510)
        self.assertNotEqual(addresses["current"], game_base + 0x21550)

    def test_descriptor_location_constants_match_the_audited_disassembly(self):
        """描述符的定位方式必须与 `const Level::GetRoomByIdx @ 0x3dc3b0` 的反汇编一致。

        那段代码是：`槽号 = 表[维度×169 + 索引]`（表在基址+0x2d18），
        然后 `描述符 = 基址 + 槽号×0x100 + 0x18`。第一版把它当成"堆上的 vector"去找，
        在真机上读到一堆小整数（看起来有值、其实全错）。
        """
        self.assertEqual(self.tool.DESCRIPTOR_STRIDE, 0x100)
        self.assertEqual(self.tool.DESCRIPTOR_ARRAY_BASE, 0x18)
        self.assertEqual(self.tool.SLOT_TABLE_OFFSET, 0x2D18)
        self.assertEqual(self.tool.SLOT_ROOM_COUNT, 169)
        # 槽号为负 ⇒ 没有这个房间（引擎返回模块里的全局空描述符）。
        self.assertEqual(self.tool.descriptor_address_from_slot(0x1000, -1), 0)
        self.assertEqual(self.tool.descriptor_address_from_slot(0x1000, 3), 0x1000 + 0x18 + 3 * 0x100)

    def test_offline_replay_renders_the_scan_table(self):
        """`--from-raw` 必须能离线渲染一次扫描读数。

        对应真实用法：真机取数时先落盘，之后换解释口径**离线重放**，不再连设备
        （调试桩的 attach 预算是有限的）。样例用真机上确证过的形状：
        `+0x00` 是网格下标、未分配槽位是 0xFFFFFFFF。
        """
        def slot(grid: int, flag: int) -> list[int]:
            words = [0] * 16
            words[0] = grid | (grid << 32)          # +0x00 / +0x04 = GridIndex / SafeGridIndex
            words[0x48 // 8] = flag
            return words

        sample = {
            "note": "clear=no", "room_index": 84, "dimension": 0, "slot": 7,
            "descriptor": 0x1000, "game": 0x800, "raw_words": {"slot7": slot(84, 5)},
            "types": {"slot7": 1},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), "--from-raw", str(path)],
                text=True, capture_output=True, cwd=ROOT,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clear=no", result.stdout)
        self.assertIn("slot7 的 0x00 = 84", result.stdout)
        # 房间类型要按 PC 的 `RoomType` 表报名字：1 = ROOM_DEFAULT（普通房）。
        # 改这一条的理由（2026-09-15，用户已确认）：原来的断言钉的是 `Type=1 起始房间`，
        # 而 1 是普通房、一层楼里有好几个 —— 那条标签会把多个槽位都标成"起始房间"，
        # 是**会误导判断的错标签**。认"当前房间"要看 `+0x00`，不看 Type。
        self.assertIn("ROOM_DEFAULT", result.stdout)
        self.assertIn("这个槽位的 +0x00 = 当前房间索引", result.stdout)

    def test_compare_reports_the_field_that_flips(self):
        """`--compare` 是给 `Clear` 定名的工具：它必须只报**变化了**的偏移。

        这条门禁的意义：`Clear` 的判定完全依赖"清房间前后哪个字段翻转"，
        如果对比逻辑把没变的字段也报出来（或者漏掉变化的），定名就会挂到错误的偏移上。
        """
        def slot(grid: int, flag: int) -> list[int]:
            words = [0] * 16
            words[0] = grid | (grid << 32)
            words[0x48 // 8] = flag
            return words

        base = {"note": "C:未清房", "room_index": 84, "dimension": 0,
                "raw_words": {"slot7": slot(84, 0), "slot8": slot(71, 0)}}
        after = {"note": "D:已清房", "room_index": 84, "dimension": 0,
                 "raw_words": {"slot7": slot(84, 1), "slot8": slot(71, 0)}}
        with tempfile.TemporaryDirectory() as temporary:
            left = Path(temporary) / "c.json"
            right = Path(temporary) / "d.json"
            left.write_text(json.dumps(base), encoding="utf-8")
            right.write_text(json.dumps(after), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), "--from-raw", str(left), "--compare", str(right)],
                text=True, capture_output=True, cwd=ROOT,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("房(84) 偏移 0x48：0 → 1", result.stdout)
        self.assertNotIn("房(71)", result.stdout)

    def test_a_reading_without_descriptor_bytes_says_so(self):
        """没有描述符字节时不许编内容 —— 明说"这次读数没有落盘的描述符字节"。"""
        sample = {"note": "empty", "room_index": 7, "dimension": 0, "slot": -1,
                  "descriptor": 0, "game": 0x1000, "raw_words": {}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sample.json"
            path.write_text(json.dumps(sample), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), "--from-raw", str(path)],
                text=True, capture_output=True, cwd=ROOT,
            )
        self.assertEqual(result.returncode, 0)
        self.assertIn("没有落盘的描述符字节", result.stdout)


if __name__ == "__main__":
    unittest.main()
