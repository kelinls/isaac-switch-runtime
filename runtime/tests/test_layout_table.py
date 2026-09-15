"""布局表的门禁：证据必须能重新证明，生成物不许漂移，命名口径不许放宽。

三件事各自对应一类会真实发生的错误：

1. **证据复核**（`--verify`）：把表里每一条证据指向的函数**重新反汇编**，核对那条指令里
   确实出现了它声称的偏移（访问证据）或它用来定名的常量（语义证据）。
   没有这条，表格就只是"某人说它是这样"，别人只能选择相信；
2. **生成物一致**（`--check`）：C++ 头文件由表生成。没有这条，改表忘生成，实现里读的就是旧偏移——
   这是"编译通过、运行时读错字段"的经典来源；
3. **命名口径**：`confirmed` 的行必须**至少有一条语义证据**（拿字段与已知常量比较/使用），
   只有访问证据的行不能是 `confirmed` —— 否则就是"知道偏移在哪、但不知道它是什么"却给了名字。
   而 `confirmed` 之外的行**不允许**出现在生成的"对外 API 可用列表"里。

依赖固定 NRO 的那条在缺夹具时跳过（NRO 不在 git 里），纯数据与生成物检查照旧运行。
"""

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "build_layout_table.py"
TABLE = ROOT / "tools" / "layout_tables" / "room_descriptor.json"
HEADER = ROOT / "runtime" / "source" / "room_descriptor_layout.hpp"
NRO = ROOT / (
    "The Binding of Isaac_ Afterbirth+ 1.7.9b [010021C000B6A800][v524288][UPD]"
    "/Program #0/1/.nro/Repentance.nro"
)
CONFIDENCES = {"confirmed", "candidate", "structural"}


def run_tool(*arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(TOOL), "--table", str(TABLE), *arguments],
        text=True, capture_output=True, cwd=ROOT,
    )


class LayoutTableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(TABLE.read_text(encoding="utf-8"))

    def test_evidence_reproduces_against_the_fixed_nro(self):
        """每条证据都要能重新反汇编核对 —— 这是"布局表可复核"的全部意义。"""
        if not NRO.is_file():
            self.skipTest(f"缺少用户提供的固定 NRO：{NRO}")
        result = run_tool("--verify")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("通过", result.stdout)

    def test_generated_header_matches_the_table(self):
        """头文件必须与表一致（改表不改生成物 ⇒ 实现里读旧偏移）。"""
        result = run_tool("--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_every_row_declares_a_known_confidence(self):
        for row in self.table["rows"]:
            with self.subTest(row=row["name"]):
                self.assertIn(row["confidence"], CONFIDENCES)
                self.assertTrue(row["evidence"], "每行都必须带证据")
                self.assertTrue(row["semantic"], "每行都必须写清语义")

    def test_confirmed_rows_need_name_justifying_evidence(self):
        """`confirmed` 必须有一条**能证明名字**的证据：静态语义锚点，或真机行为观察。

        只有访问证据的行只能说明"偏移在这里被读写"，说明不了"它是什么"。
        允许它当 `confirmed` 就等于给字段挂上一个没人验证过的名字 —— 而读错字段
        比读不到更糟（会给出错误内容，界面上看不出异常）。

        两类都算：
          * `semantic` —— 某条指令拿该字段与一个已知常量比较（例如与 `ROOM_TREASURE = 4` 比）；
          * `device`   —— 真机上观察到该字段随**已知状态变化**而变（例如清房瞬间 0→1 ⇒ `Clear`）。
        行为证据通常比静态锚点更强，所以不要求 `confirmed` 行必须有 `semantic`。
        """
        for row in self.table["rows"]:
            if row["confidence"] != "confirmed":
                continue
            with self.subTest(row=row["name"]):
                kinds = {entry.get("kind", "access") for entry in row["evidence"]}
                self.assertTrue(
                    kinds & {"semantic", "device"},
                    f"{row['name']} 只有访问证据（{sorted(kinds)}），不能标 confirmed",
                )

    def test_only_confirmed_rows_are_offered_to_the_api(self):
        """生成的"可用字段"清单必须只含 `confirmed` 行。"""
        header = HEADER.read_text(encoding="utf-8")
        match = re.search(r"// 当前可用：(.*)", header)
        self.assertIsNotNone(match, "生成的头部缺少『当前可用』清单")
        listed = {item.strip() for item in match.group(1).split("、") if item.strip()}
        expected = {row["name"] for row in self.table["rows"] if row["confidence"] == "confirmed"}
        self.assertEqual(listed, expected)

    def test_build_id_is_the_fixed_target(self):
        """表锁定在固定版本上：换版本必须整体重做，不能沿用旧偏移。"""
        self.assertEqual(
            self.table["build_id"],
            "91C73FDD575061318D68886316AFEAC72388B2AB",
        )


if __name__ == "__main__":
    unittest.main()
