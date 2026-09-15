"""后端选择来自 domain 的**侵入点登记表**（`kHookCatalog`）：四路入口中继 + 一路 GOT 槽。

登记表是唯一真值源（路由适配器用 `CatalogBackends()` 派生 `kHookBackends`），
所以这里解析登记表本身，并顺带钉住它的几条不变量。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "src"
CATALOG = SRC / "domain" / "runtime" / "hook_catalog.hpp"
ROUTING = SRC / "infrastructure" / "hook_routing_adapter.hpp"

EXPECTED = ("ManagerUpdate", "ManagerRender", "PreGetCollectible",
            "ManagerPresent", "RebuildMountPoints", "GameStart")


def catalog_entries(text):
    """解析登记表，返回 [(挂点名, 后端, 是否必需), ...]，顺序即 HookId 顺序。"""
    body = re.search(r"kHookCatalog\s*=\s*\{\{(.*?)\}\};", text, re.S).group(1)
    rows = re.findall(
        r"\{\s*HookId::(\w+)\s*,\s*\"([^\"]*)\"\s*,\s*HookBackend::(\w+)\s*,\s*(true|false)\s*\}",
        body)
    return [(hook_id, backend, required == "true") for hook_id, _name, backend, required in rows]


class HookCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = CATALOG.read_text(encoding="utf-8")

    def test_one_row_per_hook_in_id_order(self):
        entries = catalog_entries(self.text)
        self.assertEqual([row[0] for row in entries], list(EXPECTED),
                         "登记表必须每个挂点一行、顺序与 HookId 一致")
        order = re.search(r"enum class HookId[^{]*\{(.*?)\};", self.text, re.S).group(1)
        names = re.findall(r"(\w+)\s*(?:=\s*\d+)?\s*,", order)
        self.assertEqual(names[:len(EXPECTED)], list(EXPECTED), "HookId 顺序必须与登记表一致")

    def test_backends(self):
        backends = [row[1] for row in catalog_entries(self.text)]
        self.assertEqual(
            backends,
            ["EntryRelay", "EntryRelay", "EntryRelay", "GotSlot", "EntryRelay", "RelaySlot"],
            "M2b 之后：四路入口中继 + ManagerPresent 走 GOT 槽；"
            "GameStart 走 RelaySlot（引擎侧中继已放好代码，我们只往槽里写回调指针）")

    def test_exactly_one_required_point_and_it_is_manager_update(self):
        required = [row[0] for row in catalog_entries(self.text) if row[2]]
        self.assertEqual(required, ["ManagerUpdate"],
                         "只有 Manager::Update 是加载 Mod 的前置条件")

    def test_routing_table_is_derived_not_duplicated(self):
        routing = ROUTING.read_text(encoding="utf-8")
        self.assertIn("kHookBackends = CatalogBackends()", routing,
                      "路由表必须由登记表派生，不能出现第二处定义")
        self.assertNotIn("HookBackend::EntryRelay,", routing,
                         "路由表里不得再逐个列出后端（真值源在登记表）")

    def test_routing_adapter_delegates_to_all_backends(self):
        cpp = (ROUTING.parent / "hook_routing_adapter.cpp").read_text(encoding="utf-8")
        self.assertIn("kHookBackends", cpp)
        for member in ("legacy_", "relay_", "gotSlot_"):
            self.assertIn(member, cpp)


if __name__ == "__main__":
    unittest.main()
