"""读数工具的"偏移表自动同步"门禁（**不需要设备**）。

为什么值得单独立测试：这套工具的偏移以前是**手抄**的，而改任何一个编译单元都会让
`bss`/`data` 整体挪位 —— 项目已经因此误读过两次（读数"看起来有值"、其实是别人的变量）。
现在改成"读数前直接问本地 ELF 的符号表"，那么就要钉住三件事：

1. ELF 解析本身对不对（用一个**自己拼出来的最小 ELF** 验，不依赖构建产物）；
2. 解析不到的符号必须**从表里删掉**，绝不允许沿用过期的偏移；
3. 提示表（键 → 符号名）本身不许写空、写重，也不许指向已经不存在的键。
"""

import importlib.util
import struct
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
GDB_TOOL = ROOT / "tools" / "read_device_state_via_gdb.py"
ELF_TOOL = ROOT / "tools" / "elf_syms.py"
ERROR_PROBE = ROOT / "tools" / "probe_lua_error_channel.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_minimal_elf(symbols: dict, payload: bytes = b"\x11" * 32, text_vaddr: int = 0x1000,
                      text_offset: int = 0x200) -> bytes:
    """拼一个"够解析"的 ELF64：一个 PT_LOAD + .text/.symtab/.strtab/.shstrtab。

    自己拼而不是用真构建产物：门禁要在**没有构建过**的机器上也能跑（`runtime.elf` 是被
    gitignore 的产物）。
    """
    shstr = b"\0.text\0.symtab\0.strtab\0.shstrtab\0"
    names = {".text": shstr.index(b".text"), ".symtab": shstr.index(b".symtab"),
             ".strtab": shstr.index(b".strtab"), ".shstrtab": shstr.index(b".shstrtab")}

    strtab = bytearray(b"\0")
    entries = []
    for name, (value, size) in symbols.items():
        off = len(strtab)
        strtab += name.encode() + b"\0"
        entries.append((off, value, size))

    symtab = bytearray(24)                      # 第一项是空符号（ELF 规定）
    for off, value, size in entries:
        symtab += struct.pack("<IBBHQQ", off, 0x10, 0, 1, value, size)

    payload_offset = text_offset
    symtab_offset = payload_offset + len(payload)
    strtab_offset = symtab_offset + len(symtab)
    shstr_offset = strtab_offset + len(strtab)
    shoff = (shstr_offset + len(shstr) + 7) & ~7
    shnum = 5                                   # null + .text/.symtab/.strtab/.shstrtab
    total = shoff + shnum * 64

    blob = bytearray(total)
    blob[0:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
    struct.pack_into("<HHIQQQIHHHHHH", blob, 0x10,
                     1, 0xB7, 1, text_vaddr, 64, shoff, 0, 64, 56, 1, 64, shnum, 4)
    struct.pack_into("<IIQQQQQQ", blob, 64, 1, 5, text_offset, text_vaddr, 0,
                     len(payload), len(payload), 8)

    def section(index: int, name_off: int, kind: int, addr: int, offset: int, size: int,
                link: int, align: int, entsize: int) -> None:
        struct.pack_into("<IIQQQQIIQQ", blob, shoff + index * 64,
                         name_off, kind, 0, addr, offset, size, link, 0, align, entsize)

    section(1, names[".text"], 1, text_vaddr, payload_offset, len(payload), 0, 4, 0)
    section(2, names[".symtab"], 2, 0, symtab_offset, len(symtab), 3, 8, 24)
    section(3, names[".strtab"], 3, 0, strtab_offset, len(strtab), 0, 1, 0)
    section(4, names[".shstrtab"], 3, 0, shstr_offset, len(shstr), 0, 1, 0)
    blob[payload_offset:payload_offset + len(payload)] = payload
    blob[symtab_offset:symtab_offset + len(symtab)] = symtab
    blob[strtab_offset:strtab_offset + len(strtab)] = strtab
    blob[shstr_offset:shstr_offset + len(shstr)] = shstr
    return bytes(blob)


class ElfSymTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.elf_syms = load_module("elf_syms", ELF_TOOL)

    def test_load_symbols_reads_names_and_addresses(self):
        blob = build_minimal_elf({"IsaacModRuntime_GetRuntimeIdentity": (0x28bd8, 68),
                                  "g_LastLuaErrorText": (0x336ca8, 256)})
        symbols = self.elf_syms.load_symbols(blob)
        self.assertEqual(symbols["IsaacModRuntime_GetRuntimeIdentity"], (0x28bd8, 68))
        self.assertEqual(symbols["g_LastLuaErrorText"], (0x336ca8, 256))

    def test_read_vaddr_translates_through_program_headers(self):
        """`read_vaddr` 要把"模块内偏移"换成文件偏移（自校验指纹就是这么取的）。"""
        payload = bytes(range(32))
        blob = build_minimal_elf({}, payload=payload, text_vaddr=0x1000, text_offset=0x200)
        self.assertEqual(self.elf_syms.read_vaddr(blob, 0x1000, 8), bytes(range(8)))
        self.assertEqual(self.elf_syms.read_vaddr(blob, 0x1008, 4), payload[8:12])
        self.assertIsNone(self.elf_syms.read_vaddr(blob, 0x999999, 4))

    def test_stripped_elf_reports_no_symbols_instead_of_guessing(self):
        blob = bytearray(build_minimal_elf({"x": (0x10, 4)}))
        # 把 `.symtab` 这个节**改名**（名字置空），模拟没有符号表的产物：
        # 解析器按节名找 `.symtab`，于是应当老实返回"没有符号"而不是瞎猜。
        shoff, = struct.unpack_from("<Q", blob, 0x28)
        struct.pack_into("<I", blob, shoff + 2 * 64, 0)
        self.assertEqual(self.elf_syms.load_symbols(bytes(blob)), {})


class SymSyncTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tool = load_module("read_device_state_via_gdb", GDB_TOOL)

    def setUp(self):
        self.original = dict(self.tool.SYMS)

    def tearDown(self):
        self.tool.SYMS.clear()
        self.tool.SYMS.update(self.original)

    def test_sync_takes_offsets_from_the_elf(self):
        key = "identity"
        name = self.tool.SYM_ELF_NAMES[key]
        with self._elf({name: (0x1234, 68)}) as path:
            self.tool.sync_syms_from_elf(path)
        self.assertEqual(self.tool.SYMS[key], 0x1234)

    def test_missing_symbol_is_dropped_not_kept_stale(self):
        """核心断言：解析不到就**删键**。留着过期偏移等于把别人的变量当成我们的。"""
        kept, gone = "identity", "g_DefaultEntry"
        with self._elf({self.tool.SYM_ELF_NAMES[kept]: (0x1234, 68)}) as path:
            missing = self.tool.sync_syms_from_elf(path)
        self.assertIn(gone, missing)
        self.assertNotIn(gone, self.tool.SYMS)
        self.assertEqual(self.tool.SYMS[kept], 0x1234)

    def test_no_elf_keeps_the_table_and_says_so(self):
        with self._elf({}) as path:
            path.unlink()
            missing = self.tool.sync_syms_from_elf(path)
        self.assertTrue(missing)
        self.assertEqual(self.tool.SYMS, self.original)

    def _elf(self, symbols):
        import tempfile

        class _Fixture:
            def __enter__(self_inner):
                self_inner._dir = tempfile.TemporaryDirectory()
                target = Path(self_inner._dir.name) / "runtime.elf"
                target.write_bytes(build_minimal_elf(symbols))
                return target

            def __exit__(self_inner, *_exc):
                self_inner._dir.cleanup()
                return False

        return _Fixture()


class SymbolHintTests(unittest.TestCase):
    """提示表本身的完整性：写错了会让整张偏移表静默错位。"""

    @classmethod
    def setUpClass(cls):
        cls.tool = load_module("read_device_state_via_gdb_hints", GDB_TOOL)
        cls.probe = load_module("probe_lua_error_channel", ERROR_PROBE)

    def test_every_hint_key_is_readable_after_sync(self):
        """同步之后，提示表里的每个键都必须真的在 `SYMS` 里 —— 否则读数会 KeyError 或读错位置。

        注意：错误通道那几个键（`g_CallbackError` 等）**只在提示表里**，值一律由 ELF 提供；
        这正是我们要的：它们的字面值没有任何理由手抄一份。
        """
        elf = ROOT / self.tool.DEFAULT_ELF
        if not elf.exists():
            self.skipTest(f"没有本地构建产物 {self.tool.DEFAULT_ELF}")
        self.tool.sync_syms_from_elf(elf)
        absent = [key for key in self.tool.SYM_ELF_NAMES if key not in self.tool.SYMS]
        self.assertEqual(absent, [], f"同步后仍缺失：{absent}")

    def test_hints_are_unique_and_non_empty(self):
        names = list(self.tool.SYM_ELF_NAMES.values())
        self.assertTrue(all(names))
        self.assertEqual(len(names), len(set(names)), "同一个符号名被挂到两个键上")

    def test_error_probe_keys_are_all_auto_synced(self):
        """错误通道探针读的每个键都必须能被自动同步。

        否则它会用**手抄的过期偏移**去读 —— 这正是这个探针以前读不出东西的原因之一
        （旧版还在调用早已不存在的接口）。
        """
        for key in self.probe.WANTED:
            self.assertIn(key, self.tool.SYM_ELF_NAMES,
                          f"{key} 没进提示表 ⇒ 探针会读到过期偏移")

    def test_error_probe_decodes_module_name_heads(self):
        word = int.from_bytes(b"main.lua", "big")
        self.assertEqual(self.probe.ascii_of(word), "main.lua")

    def test_current_build_elf_resolves_every_hint(self):
        """有构建产物时顺带核对一次：整张提示表都要能在**当前这份 ELF** 里解析到。

        这条是"偏移表不许过期"的守门人：重建之后若某个符号改了名/没了，这里会红。
        """
        elf = ROOT / self.tool.DEFAULT_ELF
        if not elf.exists():
            self.skipTest(f"没有本地构建产物 {self.tool.DEFAULT_ELF}")
        elf_syms = load_module("elf_syms_for_gate", ELF_TOOL)
        table = elf_syms.load_symbols(elf.read_bytes())
        unresolved = {key: name for key, name in self.tool.SYM_ELF_NAMES.items()
                      if name not in table}
        self.assertEqual(unresolved, {}, f"这些符号在本构建的 ELF 里找不到：{unresolved}")


if __name__ == "__main__":
    unittest.main()
