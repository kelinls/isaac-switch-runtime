"""The SaltyNX host plugin must stay loadable.

Build `20260910670000` shipped a plugin that SaltyNX could not load, and the
console crashed with `Result 0x2A8 (2168-0001)` / `Instruction Abort` at `PC=0x60`.
The cause was structural rather than logical: the plugin held its resolver through a
polymorphic base-class reference, so `resolver_.Find(...)` became a virtual call, and
the vtable landed in `.data` behind `R_AARCH64_RELATIVE` relocations that
`SaltySDCore_DynamicLinkModule` does not apply.

These tests protect the two properties that keep the artifact loadable:

* the plugin source contains no virtual function, and binds its one import through a
  global function pointer instead of a PLT call;
* the build refuses to publish an artifact that violates either rule.

`tools/check_saltynx_plugin_elf.py` is the gate, so it is itself exercised here
against deliberately malformed synthetic ELFs -- a gate that cannot fail is worse
than no gate.
"""
import struct
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT.parent
PLUGIN_ROOT = ROOT / "src" / "host_plugin"
CHECKER = REPO / "tools" / "check_saltynx_plugin_elf.py"
POST_BUILD = ROOT / "misc" / "scripts" / "post-build.sh"
STAGE146_ARTIFACT = (
    ROOT / "deploy-saltynx" / "stage146" / "SaltySD" / "plugins" / "010021C000B6A000" / "isaac-runtime.elf"
)

R_AARCH64_ABS64 = 257
R_AARCH64_GLOB_DAT = 1025
R_AARCH64_JUMP_SLOT = 1026
R_AARCH64_RELATIVE = 1027

sys.path.insert(0, str(REPO))
from tools import check_saltynx_plugin_elf as gate  # noqa: E402


def _build_elf(*, relocations, undefined, need_plt=False, relative_count=0):
    """Emit a minimal ELF64/AArch64 shared object with the requested dynamic shape.

    Only the pieces `check_saltynx_plugin_elf.inspect` reads are populated: the ELF
    header, `.dynstr`, `.dynsym`, `.rela.dyn`, optional `.rela.plt` and `.plt`, and the
    section-header table.
    """
    dynstr = bytearray(b"\0")
    name_offset: dict[str, int] = {}

    def intern_str(name: str) -> int:
        if name not in name_offset:
            name_offset[name] = len(dynstr)
            dynstr.extend(name.encode() + b"\0")
        return name_offset[name]

    symbols: list[tuple[str, int, int]] = [(name, 1, 0) for name in undefined]
    for _, symbol in relocations:
        if symbol is not None and symbol not in undefined:
            symbols.append((symbol, 1, 3))
    for name, _, _ in symbols:
        intern_str(name)

    dynsym = bytearray(struct.pack("<IBBHQQ", 0, 0, 0, 0, 0, 0))
    symbol_index: dict[str, int] = {}
    for index, (name, binding, section_index) in enumerate(symbols, start=1):
        symbol_index[name] = index
        dynsym.extend(
            struct.pack("<IBBHQQ", name_offset[name], (binding << 4) | 2, 0, section_index, 0, 0)
        )

    rela = bytearray()
    for kind, symbol in relocations:
        slot = 0 if symbol is None else symbol_index[symbol]
        rela.extend(struct.pack("<QQq", 0x1000 + len(rela), (slot << 32) | kind, 0x40))
    for _ in range(relative_count):
        rela.extend(struct.pack("<QQq", 0x2000, R_AARCH64_RELATIVE, 0x60))

    shstr = bytearray(b"\0")
    shstr_offset: dict[str, int] = {}

    def intern_section(name: str) -> None:
        shstr_offset[name] = len(shstr)
        shstr.extend(name.encode() + b"\0")

    # Section order: 0 NULL, 1 .dynstr, 2 .dynsym, 3 .rela.dyn, [.rela.plt], [.plt], .shstrtab.
    intern_section("")
    next_index = 1
    dynstr_index = next_index
    intern_section(".dynstr")
    next_index += 1
    dynsym_index = next_index
    intern_section(".dynsym")
    next_index += 1
    rela_dyn_index = next_index
    intern_section(".rela.dyn")
    next_index += 1
    rela_plt_index = None
    plt_index = None
    if need_plt:
        rela_plt_index = next_index
        intern_section(".rela.plt")
        next_index += 1
        plt_index = next_index
        intern_section(".plt")
        next_index += 1
    shstrtab_index = next_index
    intern_section(".shstrtab")
    section_count = next_index + 1

    blobs: list[tuple[int, bytes]] = [
        (dynstr_index, bytes(dynstr)),
        (dynsym_index, bytes(dynsym)),
        (rela_dyn_index, bytes(rela)),
    ]
    if need_plt:
        blobs.append((rela_plt_index, struct.pack("<QQq", 0x2108, (1 << 32) | R_AARCH64_JUMP_SLOT, 0)))
        blobs.append((plt_index, bytes(32)))
    blobs.append((shstrtab_index, bytes(shstr)))

    body = bytearray()
    layout: dict[int, tuple[int, int]] = {}
    cursor = 0x40
    for index, data in blobs:
        cursor = (cursor + 7) // 8 * 8
        layout[index] = (cursor, len(data))
        body.extend(b"\0" * (cursor - (0x40 + len(body))))
        body.extend(data)
        cursor += len(data)

    section_headers_offset = (0x40 + len(body) + 7) // 8 * 8

    def section_header(index, name, kind, link, entsize):
        offset, size = layout.get(index, (0, 0))
        return struct.pack(
            "<IIQQQQIIQQ", shstr_offset[name], kind, 0, 0, offset, size, link, 0, 8, entsize
        )

    headers = bytearray(struct.pack("<IIQQQQIIQQ", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    headers.extend(section_header(dynstr_index, ".dynstr", 3, 0, 0))
    headers.extend(section_header(dynsym_index, ".dynsym", 11, dynstr_index, 24))
    headers.extend(section_header(rela_dyn_index, ".rela.dyn", 4, dynsym_index, 24))
    if need_plt:
        headers.extend(section_header(rela_plt_index, ".rela.plt", 4, dynsym_index, 24))
        headers.extend(section_header(plt_index, ".plt", 1, 0, 16))
    headers.extend(section_header(shstrtab_index, ".shstrtab", 3, 0, 1))

    ident = b"\x7fELF" + bytes([2, 1, 1, 0]) + b"\0" * 8
    header = ident + struct.pack(
        "<HHIQQQIHHHHHH",
        3,  # ET_DYN
        183,  # EM_AARCH64
        1,
        0,
        0,
        section_headers_offset,
        0,
        64,
        56,
        0,
        64,
        section_count,
        shstrtab_index,
    )
    binary = bytearray(header)
    binary.extend(body)
    binary.extend(b"\0" * (section_headers_offset - len(binary)))
    binary.extend(headers)
    return bytes(binary)


class CheckerTests(unittest.TestCase):
    """The gate must accept the proven shape and reject both failure modes."""

    def _inspect(self, binary: bytes):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "plugin.elf"
            artifact.write_bytes(binary)
            return gate.inspect(artifact)

    def test_accepts_the_shape_that_is_proven_on_hardware(self):
        binary = _build_elf(
            relocations=[(R_AARCH64_ABS64, "SaltySDCore_FindSymbol")],
            undefined=["SaltySDCore_FindSymbol"],
        )

        problems, summary = self._inspect(binary)

        self.assertEqual(problems, [])
        self.assertEqual(summary["relocation_kinds"], [R_AARCH64_ABS64])
        self.assertEqual(summary["undefined_symbols"], ["SaltySDCore_FindSymbol"])

    def test_rejects_relative_relocations_which_saltynx_never_applies(self):
        binary = _build_elf(
            relocations=[(R_AARCH64_ABS64, "SaltySDCore_FindSymbol")],
            undefined=["SaltySDCore_FindSymbol"],
            relative_count=3,
        )

        problems, _ = self._inspect(binary)

        self.assertTrue(any("3 x R_AARCH64_RELATIVE" in problem for problem in problems), problems)

    def test_rejects_a_plt_which_means_the_import_was_called_directly(self):
        binary = _build_elf(
            relocations=[(R_AARCH64_ABS64, "SaltySDCore_FindSymbol")],
            undefined=["SaltySDCore_FindSymbol"],
            need_plt=True,
        )

        problems, _ = self._inspect(binary)

        self.assertTrue(any(problem.startswith(".plt is present") for problem in problems), problems)

    def test_rejects_the_newlib_allocator_that_a_virtual_destructor_drags_in(self):
        binary = _build_elf(
            relocations=[(R_AARCH64_ABS64, "SaltySDCore_FindSymbol")],
            undefined=["SaltySDCore_FindSymbol", "free", "_sbrk_r"],
        )

        problems, _ = self._inspect(binary)

        self.assertTrue(any("newlib's free" in problem for problem in problems), problems)
        self.assertTrue(any("newlib's _sbrk_r" in problem for problem in problems), problems)

    def test_rejects_an_engineered_import_beyond_the_single_core_lookup(self):
        binary = _build_elf(
            relocations=[(R_AARCH64_ABS64, "SaltySDCore_FindSymbol")],
            undefined=["SaltySDCore_FindSymbol", "SaltySDCore_printf"],
        )

        problems, _ = self._inspect(binary)

        self.assertTrue(any("undefined-symbol set" in problem for problem in problems), problems)


class HostPluginSourceTests(unittest.TestCase):
    """Keep the source shape that produces a loadable artifact."""

    def test_plugin_headers_declare_no_virtual_function(self):
        offenders = []
        for header in sorted(PLUGIN_ROOT.glob("*.hpp")):
            for number, raw in enumerate(header.read_text(encoding="utf-8").splitlines(), start=1):
                code = raw.split("//", 1)[0]
                if "virtual" in code:
                    offenders.append(f"{header.name}:{number}: {raw.strip()}")

        self.assertEqual(offenders, [], "a virtual function puts a vtable in .data, which SaltyNX cannot relocate")

    def test_resolver_binds_its_import_through_a_global_function_pointer(self):
        source = (PLUGIN_ROOT / "saltynx_symbol_resolver.cpp").read_text(encoding="utf-8")

        self.assertIn("g_find_symbol = &SaltySDCore_FindSymbol", source)
        self.assertIn("return g_find_symbol(name);", source)
        self.assertNotIn("return SaltySDCore_FindSymbol(name);", source)

    def test_host_plugin_holds_the_resolver_by_concrete_type(self):
        header = (PLUGIN_ROOT / "saltynx_host_plugin.hpp").read_text(encoding="utf-8")

        self.assertIn("SaltyNxCoreResolver& resolver_;", header)
        self.assertNotIn("ISaltyNxSymbolResolver", header)


class BuildGateWiringTests(unittest.TestCase):
    """The gate has to run during the build, not only when tests are run."""

    def test_post_build_checks_every_plugin_before_publishing_it(self):
        script = POST_BUILD.read_text(encoding="utf-8")

        self.assertIn("tools/check_saltynx_plugin_elf.py", script)
        self.assertIn('if [ -n "${SALTYNX_PLUGIN_OUTPUT:-}" ]; then', script)
        self.assertLess(
            script.index("check_saltynx_plugin_elf.py"),
            script.index('cp -- "${EXL_ARTIFACT_DIR}/${NAME}.elf" "${SALTYNX_PLUGIN_OUTPUT}"'),
            "the gate must run before the plugin is copied into the deploy tree",
        )

    def test_built_stage146_artifact_is_loadable(self):
        if not STAGE146_ARTIFACT.is_file():
            self.skipTest("build stage146 (make host_plugin HOST_PLUGIN_STAGE=146) to validate the artifact")

        problems, summary = gate.inspect(STAGE146_ARTIFACT)

        self.assertEqual(problems, [])
        self.assertEqual(summary["relocation_kinds"], [R_AARCH64_ABS64])
        self.assertEqual(summary["undefined_symbols"], ["SaltySDCore_FindSymbol"])


if __name__ == "__main__":
    unittest.main()
