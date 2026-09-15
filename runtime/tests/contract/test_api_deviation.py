"""`api_deviation.cpp` 那张"与 PC 语义的偏离"表的契约门禁。

## 为什么要有这张表、这条门禁

验收基准是三维：① 存在、② 语义、③ 派发时机。其中"② 语义不满足"必须**被标记出来**。
在此之前偏离只写在实现处的注释里 —— 注释不会被任何报告、清单或设备读数带出来，
于是"实现正确"和"实现但有已知偏离"就分不清，批量补齐 API 时更没法逐条收口。

本文件守住四件事：

1. **每条偏离的 id 都必须在 `api_catalog.cpp` 里真实存在**（否则是打错 id 的死账）；
2. **文本必须同时说清"差在哪"和"后果"**（没有后果说明的条目无法用于判断"要不要现在修"）；
3. **条数写死在测试里**：新增/删除都必须人工过一遍（与 `test_api_catalog` 的绑定行数
   是同一套记账方式）；
4. 同一 id 不得重复登记。

（与项目其它源码门禁同一形态：只做受控文本提取，不做 C++ 解析。）
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "runtime" / "src" / "interfaces" / "lua" / "api_catalog.cpp"
DEVIATIONS = ROOT / "runtime" / "src" / "interfaces" / "lua" / "api_deviation.cpp"

#: 当前表里的条数。新增或删除偏离条目时**必须**同步这个数字并在上面补一行说明。
#:
#: 首次建立（2026-09-15，地基一期）共 22 条，来源是代码注释里**明确承认**偏离的地方：
#:   `Options.HUDOffset`（1）；`Game:GetSeeds`/`GetVictoryLap` 与 `Seeds:IsCustomRun`/
#:   `GetStartSeed`（4）；`Level:GetCurses`（1）；`Room` 网格与寻路六条（6）；
#:   `Sprite:GetTexel`（1）；`Isaac.GetTime`（1）；`Entity`/`EntityPlayer:GetData`（2）；
#:   `ItemConfig`/`ItemConfig_Item:HasTags`（2）；`EntityPickup:IsShopItem`（1）；
#:   `EntityPlayer:GetPill`/`GetCard`（2）；`EntityPlayer:GetName`（1）。
EXPECTED_DEVIATIONS = 22

#: 允许的偏离种类（与 `ApiDeviationKind` 一致）。
KINDS = {"Partial", "Placeholder", "NotInEngine"}


def catalog_ids(text: str) -> set[int]:
    """`api_catalog.cpp` 里所有条目的完整 id。

    只算 `MakeId(...)` 调用 —— 目录里每条 API 都这么写（这是该文件的既有约定，
    `test_api_catalog` 的"家族绑定 id 必须与 Catalog 一致"那条也依赖同一形状）。
    """
    ids = set()
    domain_values = {
        "Global": 0, "Mod": 1, "Game": 2, "Level": 3, "Room": 4, "ItemPool": 5,
        "Music": 6, "Rng": 7, "Persistence": 8, "Input": 9, "Diagnostic": 10,
        "Font": 11, "Vector": 12, "Sprite": 13, "Isaac": 14, "Seed": 15,
    }
    for match in re.finditer(
        r"MakeId\(ApiDomain::(\w+),\s*(\d+),\s*(0x[0-9A-Fa-f]+)\)", text
    ):
        domain, group, sequence = match.groups()
        if domain not in domain_values:
            continue
        ids.add((domain_values[domain] << 24) | (int(group) << 16) | int(sequence, 16))
    return ids


def deviation_entries(text: str) -> list[tuple[int, str, str]]:
    """从 `api_deviation.cpp` 提取 `(id, kind, 文本)`。

    一个条目的形状是 `{0x…, ApiDeviationKind::<Kind>, "…" "…" …}` —— 文本可能跨多行拼接，
    这里的正则接受任意段落的相邻字符串字面量。
    """
    entries: list[tuple[int, str, str]] = []
    for match in re.finditer(
        r"\{\s*(0x[0-9A-Fa-f]{8})\s*,\s*ApiDeviationKind::(\w+)\s*,\s*((?:\s*\"(?:[^\"\\]|\\.)*\"\s*)+)\}",
        text,
    ):
        identifier, kind, raw = match.groups()
        pieces = re.findall(r'"((?:[^"\\]|\\.)*)"', raw)
        entries.append((int(identifier, 16), kind, "".join(pieces)))
    return entries


class ApiDeviationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = CATALOG.read_text(encoding="utf-8")
        cls.deviations = DEVIATIONS.read_text(encoding="utf-8")

    def test_every_deviation_points_at_a_real_catalog_entry(self):
        """id 必须在 catalog 里真实存在 —— 打错 id 就等于这条偏离没有任何人会看到。"""
        known = catalog_ids(self.catalog)
        self.assertGreater(len(known), 150, "catalog id 解析结果太少，门禁失去意义")
        unknown = [
            hex(identifier)
            for identifier, _, _ in deviation_entries(self.deviations)
            if identifier not in known
        ]
        self.assertEqual(unknown, [], f"偏离表里出现了 catalog 中不存在的 id：{unknown}")

    def test_kinds_are_known_values(self):
        kinds = {kind for _, kind, _ in deviation_entries(self.deviations)}
        self.assertTrue(kinds, "一条偏离都没解析到 —— 门禁失去意义")
        self.assertTrue(kinds <= KINDS, f"出现了未知的偏离种类：{sorted(kinds - KINDS)}")

    def test_each_entry_states_the_consequence(self):
        """每条都必须说清后果（"偏离后果"或"只会/不会造成…"），否则无法判断要不要现在修。"""
        for identifier, _, text in deviation_entries(self.deviations):
            with self.subTest(id=hex(identifier)):
                self.assertGreater(len(text), 40, f"{hex(identifier)} 的说明太短，说不清后果")
                self.assertTrue(
                    ("后果" in text) or ("只" in text) or ("不" in text),
                    f"{hex(identifier)} 没有说清'偏离会带来什么'：{text!r}",
                )

    def test_no_duplicate_ids(self):
        identifiers = [identifier for identifier, _, _ in deviation_entries(self.deviations)]
        duplicates = sorted({value for value in identifiers if identifiers.count(value) > 1})
        self.assertEqual(duplicates, [], f"同一 id 被登记了多次：{[hex(v) for v in duplicates]}")

    def test_the_ledger_count_is_up_to_date(self):
        """条数写死 —— 新增/删除偏离时强制人工过一遍（与绑定行数同一套记账方式）。"""
        self.assertEqual(
            len(deviation_entries(self.deviations)),
            EXPECTED_DEVIATIONS,
            "偏离表的条数变了。请确认每条都写清了'差在哪 + 为什么 + 后果'，"
            "再同步 EXPECTED_DEVIATIONS 并在上面补一行说明。",
        )


if __name__ == "__main__":
    unittest.main()
