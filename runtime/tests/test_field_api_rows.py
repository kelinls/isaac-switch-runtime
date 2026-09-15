"""字段读取型 API「数据行」机制的门禁（**不需要设备**）。

这套机制的意义是**成本**：一个"读引擎字段并返回"的方法手写要 236–312 字节代码
（反汇编实测），写成数据行则是 **0 字节代码 + 16 字节只读数据**，代价是共享处理器
`FieldApiHandler` 的一次性 ~1 KB。所以它必须被钉住三件事：

1. **行本身合法**：`id` 在 catalog 里存在且 owner 对得上；`id` 不重复；不与手写绑定表撞 id
   （撞了就会出现"同一方法挂两次、以手写为准"这种难查的现象）；
2. **偏移有出处**：行里引用的必须是 `runtime_constants.hpp` 里的**具名常量**，
   不能在实现里现编一个偏移 —— 这条与地基二期"只有有证据的偏移才能进 API"一致；
3. **行真的被挂上**：`AttachOwnerMethods` 必须收到那张行表，否则方法会静默消失。
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ISAAC_API = ROOT / "runtime" / "src" / "interfaces" / "lua" / "isaac_api.cpp"
CATALOG = ROOT / "runtime" / "src" / "interfaces" / "lua" / "api_catalog.cpp"
BINDING = ROOT / "runtime" / "src" / "interfaces" / "lua" / "owner_binding.cpp"
CONSTANTS = ROOT / "runtime" / "source" / "runtime_constants.hpp"
HEADER = ROOT / "runtime" / "src" / "interfaces" / "lua" / "field_api.hpp"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def catalog_entries() -> dict[int, tuple[str, str]]:
    """`catalog id -> (owner, name)`（id 按 `MakeId(domain, group, low)` 的打包规则算）。

    `MakeId` 的位布局在 `api_catalog.cpp` 顶部：`domain << 24 | group << 16 | low`。
    """
    domain_bits = {
        "Global": 0x01, "Isaac": 0x0E, "Game": 0x02, "Level": 0x03, "Room": 0x04,
    }
    entries: dict[int, tuple[str, str]] = {}
    pattern = re.compile(
        r"\{MakeId\(ApiDomain::(\w+),\s*(\d+),\s*0x([0-9A-Fa-f]+)\),\s*ApiDomain::\w+,\s*"
        r"\"(\w+)\",\s*\"(\w+)\"")
    for match in pattern.finditer(read(CATALOG)):
        domain, group, low, owner, name = match.groups()
        top = domain_bits.get(domain, 0)
        identifier = (top << 24) | (int(group) << 16) | int(low, 16)
        entries[identifier] = (owner, name)
    return entries


def constant_values() -> dict[str, int]:
    values: dict[str, int] = {}
    pattern = re.compile(r"inline constexpr uintptr_t (k\w+Offset)\s*=\s*(0x[0-9A-Fa-f]+)")
    for match in pattern.finditer(read(CONSTANTS)):
        values[match.group(1)] = int(match.group(2), 16)
    return values


def field_rows() -> list[dict]:
    """`kFieldApis` 里的每一行（解析字段而不是执行 C++）。"""
    text = read(ISAAC_API)
    table = re.search(r"constexpr FieldApiRow kFieldApis\[\] = \{(.*?)\n\};", text, re.S)
    if table is None:
        return []
    rows = []
    offset2 = r"(?:static_cast<std::uint32_t>\(\s*)?(k\w+Offset|0x[0-9A-Fa-f]+|\d+)\s*\)?"
    pattern = (r"\{\s*(0x[0-9A-Fa-f]+)\s*,\s*(k\w+Offset)\s*,\s*"
               + offset2 + r"\s*,\s*FieldKind::(\w+)\s*,\s*FieldMissing::(\w+)\s*,"
               r"\s*FieldReceipt::(\w+)\s*,\s*(0x[0-9A-Fa-f]+|\d+)\s*\}")
    for match in re.finditer(pattern, table.group(1)):
        rows.append({
            "id": int(match.group(1), 16),
            "offset_name": match.group(2),
            "offset2": match.group(3),
            "kind": match.group(4),
            "missing": match.group(5),
            "receipt": match.group(6),
            "probe": match.group(7),
        })
    return rows


class FieldApiRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = field_rows()
        cls.catalog = catalog_entries()
        cls.constants = constant_values()

    def test_the_table_is_not_empty_and_parses(self):
        """行表必须真的解析出内容 —— 解析器失效会让下面几条"全绿但什么都没查"。"""
        self.assertGreaterEqual(len(self.rows), 4, f"只解析到 {len(self.rows)} 行")

    #: 接收者族 → 允许的 catalog owner。同一张数据行表服务多个族（绑定循环按 id 匹配），
    #: 所以这里按 `receipt` 判断 owner 对不对，而不是假定全是 EntityPlayer。
    OWNERS_BY_RECEIPT = {
        "Entity": {"Entity", "EntityPlayer", "EntityPickup"},
        "EntityPlayer": {"EntityPlayer"},
        "ItemConfigItem": {"ItemConfig_Item"},
    }

    def test_every_row_id_exists_in_the_catalog_with_the_right_owner(self):
        self.assertTrue(self.catalog, "没有解析到 catalog 条目")
        for row in self.rows:
            with self.subTest(row=hex(row["id"])):
                self.assertIn(row["id"], self.catalog,
                              f"{hex(row['id'])} 不在 catalog 里 ⇒ 这一行永远不会被挂上")
                owner, _name = self.catalog[row["id"]]
                allowed = self.OWNERS_BY_RECEIPT[row["receipt"]]
                self.assertIn(owner, allowed,
                              f"行说接收者是 {row['receipt']}，catalog 里的 owner 却是 {owner}")

    def test_ids_are_unique_within_the_table(self):
        ids = [row["id"] for row in self.rows]
        self.assertEqual(len(ids), len(set(ids)), "行表里有重复 id")

    def test_row_ids_do_not_collide_with_hand_written_bindings(self):
        """同一个 id 既在行表又在手写绑定表里 ⇒ 手写优先、行被静默忽略。

        这种"改了行却没生效"的现象很难查，所以直接禁掉：迁移一个方法就把手写那行删掉。
        """
        hand_written = set(int(value, 16) for value in re.findall(
            r"\{\s*(0x0E01[0-9A-Fa-f]{4})\s*,\s*&(?:EntityPlayer|ItemConfigItem)\w+\s*\}",
            read(ISAAC_API)))
        overlap = sorted(set(row["id"] for row in self.rows) & hand_written)
        self.assertEqual(overlap, [], f"这些 id 同时挂在两处：{[hex(v) for v in overlap]}")

    def test_offsets_are_named_constants_not_magic_numbers(self):
        """偏移必须引用 `runtime_constants.hpp` 里的具名常量（有出处），不许写字面量。"""
        for row in self.rows:
            with self.subTest(row=hex(row["id"])):
                self.assertIn(row["offset_name"], self.constants,
                              f"{row['offset_name']} 不在 runtime_constants.hpp 里")

    def test_offsets_stay_inside_the_entity_player_object(self):
        """偏移要落在 `Entity_Player` 对象范围内（明显越界的偏移一定是抄错了）。"""
        limit = 0x4000          # `kEntityPlayerSize` 量级；越界即视为抄错
        for row in self.rows:
            with self.subTest(row=hex(row["id"])):
                value = self.constants[row["offset_name"]]
                self.assertLess(value, limit, f"{row['offset_name']} = {hex(value)} 越界")

    def test_rows_are_registered_through_attach_owner_methods(self):
        """行表必须真的传给 `AttachOwnerMethods`，否则整张表是死数据。"""
        text = read(ISAAC_API)
        self.assertRegex(text, r"AttachOwnerMethods\(state, kEntityPlayerOwner,[^;]*kFieldApis",
                         "EntityPlayer 族的行表没有传给 AttachOwnerMethods ⇒ 那些方法根本不会被挂上")
        self.assertRegex(text, r"AttachOwnerMethods\(state, kItemConfigItemOwner,[^;]*kFieldApis",
                         "ItemConfig_Item 族的行表没有挂上（批次 7 起两个族共用同一张表）")

    def test_binding_mechanism_supports_rows_with_a_closure_upvalue(self):
        """绑定机制要按"闭包 + 行作为上值"注册，而不是把行指针忘了传。"""
        text = read(BINDING)
        self.assertIn("lua_pushlightuserdata", text)
        self.assertIn("lua_pushcclosure", text)
        self.assertIn("FieldApiHandler", text)

    def test_row_size_stays_sixteen_bytes(self):
        """行必须是 16 字节 —— 编译期已由 `static_assert` 钉住，这里再确认它还在。"""
        self.assertIn("static_assert(sizeof(FieldApiRow) == 16", read(HEADER))


if __name__ == "__main__":
    unittest.main()
