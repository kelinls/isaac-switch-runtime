"""`tools/extract_struct_layout.py` 的行为门禁（布局表配方的第一条门禁）。

这个工具产出的是**布局表的证据**：某个字段在第几字节、多宽、依据是哪条指令。
它一旦算错偏移，错误会直接变成实现里的读值逻辑（读错字段 = 给出错误内容，
比读不到更糟）。所以这里把几件已经用真产物核对过的事实钉死：

1. `GameState::write_Room` 是"逐个字段序列化"的密集来源 —— 必须能抽出 ≥20 次字段读，
   且包含开头四个 u32（0x0/0x4/0x8/0xc）与标量区最后那个 4 字节字段（0x70）；
2. `RoomDescriptor::Reset` 里三个容器必须落在 **0x90/0x98、0xc0/0xc8、0xa8→0xb0**
   —— 这一条专门钉住 **ARM64 写回寻址**（`ldr x23, [x20, #0x90]!`）与
   **寄存器被重新赋值后基准要归零**这两处修正：修之前它们会被算成 0x98→0x150、0xc0→0x150，
   是"看起来有值、其实全错"的典型；
3. 缺 NRO 时按仓库惯例**跳过**（`analysis/`、用户 dump 都不在 git 里）。

依赖真 NRO 的用例在缺夹具时跳过；纯解析逻辑的用例（正则 + 数据流）不需要 NRO，照旧运行。
"""

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "extract_struct_layout.py"
#: 用户提供的固定 Switch dump（不在 git 里 ⇒ 缺它时跳过，见文件头）。
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)

WRITE_ROOM = ("_ZN15IsaacRepentance9GameState10write_RoomERNS_19GameStateRoomConfig"
              "ERKNS_14RoomDescriptorERNS_11GameStateIOE")
RESET = "_ZN15IsaacRepentance14RoomDescriptor5ResetEv"


def run_tool(*arguments: str) -> dict:
    result = subprocess.run(
        [sys.executable, str(TOOL), *arguments, "--json"],
        text=True, capture_output=True, cwd=ROOT,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return json.loads(result.stdout)


class ExtractStructLayoutTests(unittest.TestCase):
    def require_nro(self):
        if not NRO.is_file():
            self.skipTest(f"缺少用户提供的固定 NRO：{NRO}")

    def accesses_of(self, symbol: str, argument: int) -> list[dict]:
        self.require_nro()
        return run_tool("--nro", str(NRO), "--symbol", symbol, "--arg", str(argument))["accesses"]

    def test_serialization_function_yields_a_dense_field_sequence(self):
        """序列化函数是密集来源：一次就该给出二十来个字段的偏移与宽度。"""
        reads = [a for a in self.accesses_of(WRITE_ROOM, 2) if a["direction"] == "read"]
        self.assertGreaterEqual(len(reads), 20, f"只抽到 {len(reads)} 次字段读")
        offsets = {a["offset"] for a in reads}
        for expected in (0x0, 0x4, 0x8, 0xC, 0x70):
            with self.subTest(offset=hex(expected)):
                self.assertIn(expected, offsets)
        # 宽度要按指令区分（`ldrh` 是 2 字节、`ldr w` 是 4、`ldr x` 是 8）。
        widths = {a["offset"]: a["width"] for a in reads}
        self.assertEqual(widths[0x0], 4)
        self.assertEqual(widths[0x54], 2)
        self.assertEqual(widths[0x78], 8)

    def test_writeback_addressing_keeps_later_offsets_correct(self):
        """回归：`ldr xN, [base, #imm]!` 之后，base 自身也被加了 imm。

        `RoomDescriptor::Reset` 里三处容器读就是这个形态。修之前它们会被算成
        0x98 之后的访问全部偏移 0x90（例如第二个容器读成 0x150）——**看起来有值、其实全错**，
        而这类错值会直接变成实现里的读值逻辑。所以这条要逐对钉住。
        """
        reads = [a for a in self.accesses_of(RESET, 0) if a["direction"] == "read"]
        pairs = {}
        for access in reads:
            pairs.setdefault(access["offset"], access["width"])
        # 两对 vector 的 begin/end，以及一个 libc++ string 的 data/长度。
        for offset in (0x90, 0x98, 0xC0, 0xC8, 0xB0):
            with self.subTest(offset=hex(offset)):
                self.assertIn(offset, pairs, f"写回寻址算错时会丢掉 {hex(offset)}")
        self.assertNotIn(0x150, pairs, "0x150 是写回没处理时的错值，不该出现")

    def test_missing_artifact_reports_a_clear_error(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--nro", "does-not-exist.nro", "--reg", "x19"],
            text=True, capture_output=True, cwd=ROOT,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("找不到 NRO", result.stderr)


if __name__ == "__main__":
    unittest.main()
