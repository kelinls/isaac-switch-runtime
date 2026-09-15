import os
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "source"


class SaltyNxExternalPluginStage133Tests(unittest.TestCase):
    def test_stage133_uses_saltynx_compatible_4k_segment_alignment(self):
        specs = (ROOT / "misc" / "specs" / "saltynx_plugin.specs").read_text(encoding="utf-8")

        self.assertIn("-z max-page-size=0x1000", specs)

    def test_elf_relinks_when_the_selected_specs_or_linker_script_changes(self):
        make_rules = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")

        self.assertIn("$(OUTPUT).elf: $(TOPDIR)/misc/specs/$(SPECS_NAME) $(TOPDIR)/misc/link.ld", make_rules)

    @staticmethod
    def _dynamic_symbols_and_relocations(artifact: Path):
        binary = artifact.read_bytes()
        section_offset = struct.unpack_from("<Q", binary, 0x28)[0]
        section_size = struct.unpack_from("<H", binary, 0x3A)[0]
        section_count = struct.unpack_from("<H", binary, 0x3C)[0]
        self_index = struct.unpack_from("<H", binary, 0x3E)[0]
        self_header = section_offset + self_index * section_size
        strings_offset, strings_size = struct.unpack_from("<QQ", binary, self_header + 24)
        strings = binary[strings_offset:strings_offset + strings_size]

        sections = {}
        for index in range(section_count):
            offset = section_offset + index * section_size
            name_offset, section_type = struct.unpack_from("<II", binary, offset)
            name_end = strings.find(b"\0", name_offset)
            name = strings[name_offset:name_end].decode("ascii")
            sections[name] = (offset, section_type)

        dynsym_offset, _ = sections[".dynsym"]
        dynstr_offset, _ = sections[".dynstr"]
        dynsym_data_offset, dynsym_size = struct.unpack_from("<QQ", binary, dynsym_offset + 24)
        dynsym_entry_size = struct.unpack_from("<Q", binary, dynsym_offset + 56)[0]
        dynstr_data_offset, dynstr_size = struct.unpack_from("<QQ", binary, dynstr_offset + 24)
        dynstr = binary[dynstr_data_offset:dynstr_data_offset + dynstr_size]
        symbols = []
        for index in range(dynsym_size // dynsym_entry_size):
            offset = dynsym_data_offset + index * dynsym_entry_size
            name_offset, info, _, section_index, _, _ = struct.unpack_from("<IBBHQQ", binary, offset)
            name_end = dynstr.find(b"\0", name_offset)
            symbols.append((dynstr[name_offset:name_end].decode("ascii"), info >> 4, section_index))

        rela_offset, _ = sections[".rela.dyn"]
        rela_data_offset, rela_size = struct.unpack_from("<QQ", binary, rela_offset + 24)
        rela_entry_size = struct.unpack_from("<Q", binary, rela_offset + 56)[0]
        relocations = []
        for index in range(rela_size // rela_entry_size):
            _, info, _ = struct.unpack_from("<QQq", binary, rela_data_offset + index * rela_entry_size)
            relocations.append((symbols[info >> 32][0], info & 0xFFFFFFFF))
        return symbols, relocations

    @staticmethod
    def _load_segment_alignments(artifact: Path):
        binary = artifact.read_bytes()
        program_offset = struct.unpack_from("<Q", binary, 0x20)[0]
        program_entry_size = struct.unpack_from("<H", binary, 0x36)[0]
        program_count = struct.unpack_from("<H", binary, 0x38)[0]
        alignments = []
        for index in range(program_count):
            offset = program_offset + index * program_entry_size
            segment_type = struct.unpack_from("<I", binary, offset)[0]
            if segment_type == 1:  # PT_LOAD
                alignments.append(struct.unpack_from("<Q", binary, offset + 48)[0])
        return alignments

    @staticmethod
    def _saltynx_mapping_size(artifact: Path):
        """Mirror SaltyNX 1.8.1 load_elf_proc's all-program-header span."""
        binary = artifact.read_bytes()
        program_offset = struct.unpack_from("<Q", binary, 0x20)[0]
        program_entry_size = struct.unpack_from("<H", binary, 0x36)[0]
        program_count = struct.unpack_from("<H", binary, 0x38)[0]
        minimum_vaddr = (1 << 64) - 1
        maximum_vaddr = 0
        for index in range(program_count):
            offset = program_offset + index * program_entry_size
            vaddr = struct.unpack_from("<Q", binary, offset + 16)[0]
            memory_size = struct.unpack_from("<Q", binary, offset + 40)[0]
            minimum_vaddr = min(minimum_vaddr, vaddr)
            maximum_vaddr = max(maximum_vaddr, vaddr + ((memory_size + 0xFFF) & ~0xFFF))
        return maximum_vaddr - minimum_vaddr

    def test_docker_stage133_artifact_has_only_the_salty_import(self):
        artifact_path = os.environ.get("ISAAC_SALTYNX_STAGE133_ELF")
        if artifact_path is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE133_ELF to validate a Docker-built Stage133 artifact")

        symbols, relocations = self._dynamic_symbols_and_relocations(Path(artifact_path))
        undefined_globals = {name for name, binding, section in symbols if binding == 1 and section == 0}
        self.assertEqual(undefined_globals, {"SaltySDCore_printf"})
        self.assertEqual(relocations, [("SaltySDCore_printf", 257)])
        self.assertEqual(self._load_segment_alignments(Path(artifact_path)), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(Path(artifact_path)), 0x3000)
        self.assertEqual(self._saltynx_mapping_size(Path(artifact_path)) % 0x1000, 0)

    def test_stage133_uses_a_minimal_saltynx_startup_without_exlaunch_rtld(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        startup = SOURCE / "program" / "saltynx_external_plugin_crt0.s"

        self.assertTrue(startup.is_file(), "Stage133 startup assembly is missing")
        source = startup.read_text(encoding="utf-8")
        self.assertIn("__module_start:", source)
        self.assertIn("b exl_main", source)
        self.assertIn(".ascii \"MOD0\"", source)
        self.assertNotIn("exl_relocate_self", source)
        self.assertNotIn("exl_entrypoint_init", source)
        self.assertIn("SALTYNX_STAGE133_CPPFILES := runtime_entry.cpp saltynx_external_plugin_probe.cpp", makefile)
        self.assertIn("SALTYNX_STAGE133_CFILES :=", makefile)
        self.assertIn("SALTYNX_STAGE133_SFILES := saltynx_external_plugin_crt0.s", makefile)
        self.assertIn("SPECS_NAME := saltynx_plugin.specs", makefile)

    def test_regular_runtime_excludes_stage133_only_sources(self):
        makefile = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")

        self.assertIn(
            "SFILES := $(filter-out saltynx_external_plugin_crt0.s,$(SFILES))",
            makefile,
        )
        self.assertIn(
            "CPPFILES := $(filter-out saltynx_external_plugin_probe.cpp,$(CPPFILES))",
            makefile,
        )

    def test_stage133_builds_only_a_title_scoped_saltynx_plugin(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")

        self.assertIn("SALTYNX_PLUGIN", makefile)
        self.assertIn("DIAGNOSTIC_STAGE=133", makefile)
        self.assertIn("SaltySD/plugins/$(PROGRAM_ID)/isaac-runtime.elf", makefile)
        post_build = (ROOT / "misc" / "scripts" / "post-build.sh").read_text(encoding="utf-8")
        self.assertLess(
            post_build.index('rm -rf -- "' + "$" + '{OUT}"'),
            post_build.index('if [ -n "' + "$" + '{SALTYNX_PLUGIN_OUTPUT:-}" ]'),
        )

        with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as temporary:
            fake_devkitpro = Path(temporary)
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            result = subprocess.run(
                [
                    "make",
                    "-n",
                    "clean-runtime-output",
                    "DIAGNOSTIC_STAGE=133",
                    "SALTYNX_PLUGIN=1",
                ],
                cwd=ROOT,
                env=os.environ | {"DEVKITPRO": str(fake_devkitpro)},
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("deploy-saltynx/stage133", result.stdout)
        self.assertNotIn("atmosphere/contents", result.stdout)

    def test_stage134_build_contract_includes_the_fixed_read_input(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        post_build = (ROOT / "misc" / "scripts" / "post-build.sh").read_text(encoding="utf-8")
        read_blob = ROOT / "diagnostic" / "stage134" / "isaac-runtime-read.bin"

        self.assertIn("133 134", makefile)
        self.assertEqual(read_blob.read_bytes(), b"ISAAC_STAGE134!!")
        self.assertIn("SALTYNX_STAGE134_READ_INPUT", post_build)

        with tempfile.TemporaryDirectory(prefix="fake-devkitpro-") as temporary:
            fake_devkitpro = Path(temporary)
            switch_rules = fake_devkitpro / "libnx" / "switch_rules"
            switch_rules.parent.mkdir(parents=True)
            switch_rules.write_text("%.npdm:\n\t@:\n%.nso: %.elf\n\t@:\n%.elf:\n\t@:\n")
            result = subprocess.run(
                [
                    "make",
                    "-n",
                    "clean-runtime-output",
                    "DIAGNOSTIC_STAGE=134",
                    "SALTYNX_PLUGIN=1",
                ],
                cwd=ROOT,
                env=os.environ | {"DEVKITPRO": str(fake_devkitpro)},
                text=True,
                capture_output=True,
            )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("deploy-saltynx/stage134", result.stdout)

    def test_stage133_checks_an_automatically_linked_symbol_without_io(self):
        selector = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        probe = SOURCE / "program" / "saltynx_external_plugin_probe.cpp"

        self.assertIn("EXL_DIAGNOSTIC_STAGE == 133", selector)
        self.assertIn("saltynx_external_plugin_entry.cpp", selector)
        self.assertIn("ISAAC_SP", entry)
        self.assertIn("RunSaltyNxExternalPluginProbe", entry)
        self.assertTrue(probe.is_file(), "Stage133 probe is missing")
        source = probe.read_text(encoding="utf-8")
        self.assertIn("SaltySDCore_printf", source)
        self.assertIn("RunSaltyNxExternalPluginProbe", source)
        self.assertNotIn("__attribute__((weak))", source)
        self.assertNotRegex(source, r"(?<!void )SaltySDCore_printf\(")
        for forbidden in ("smInitialize", "fsInitialize", "fopen(", "fread(", "fwrite(", "fclose("):
            self.assertNotIn(forbidden, source)

    def test_stage134_has_only_the_core_read_abi_and_a_4k_mapping_span(self):
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        probe = (SOURCE / "program" / "saltynx_external_plugin_read_probe.cpp").read_text(encoding="utf-8")
        artifact_path = os.environ.get("ISAAC_SALTYNX_STAGE134_ELF")

        self.assertIn("EXL_DIAGNOSTIC_STAGE == 134", entry)
        self.assertIn("ISAAC_SR", entry)
        for symbol in ("SaltySDCore_fopen", "SaltySDCore_fread", "SaltySDCore_fclose"):
            self.assertIn(symbol, probe)

        if artifact_path is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE134_ELF to validate a Docker-built Stage134 artifact")

        artifact = Path(artifact_path)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined_globals = {name for name, binding, section in symbols if binding == 1 and section == 0}
        self.assertEqual(undefined_globals, {
            "SaltySDCore_fopen",
            "SaltySDCore_fread",
            "SaltySDCore_fclose",
        })
        self.assertEqual({relocation_type for _, relocation_type in relocations}, {257})
        self.assertEqual({name for name, _ in relocations}, undefined_globals)
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage135_has_only_the_core_write_readback_abi(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        probe = (SOURCE / "program" / "saltynx_external_plugin_write_read_probe.cpp").read_text(encoding="utf-8")
        artifact_path = os.environ.get("ISAAC_SALTYNX_STAGE135_ELF")

        self.assertIn("133 134 135", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 135", entry)
        self.assertIn("ISAAC_SW", entry)
        for text in ("SaltySDCore_fopen", "SaltySDCore_fwrite", "SaltySDCore_fread", "SaltySDCore_fclose",
                     "ISAAC_STAGE135!!", "isaac-runtime-write.bin", '"wb"', '"rb"'):
            self.assertIn(text, probe)

        if artifact_path is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE135_ELF to validate a Docker-built Stage135 artifact")

        artifact = Path(artifact_path)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined_globals = {name for name, binding, section in symbols if binding == 1 and section == 0}
        self.assertEqual(undefined_globals, {
            "SaltySDCore_fopen",
            "SaltySDCore_fwrite",
            "SaltySDCore_fread",
            "SaltySDCore_fclose",
        })
        self.assertEqual({relocation_type for _, relocation_type in relocations}, {257})
        self.assertEqual({name for name, _ in relocations}, undefined_globals)
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage137_uses_a_versioned_multi_record_state_container(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        probe = (SOURCE / "program" / "saltynx_external_plugin_state_probe.cpp").read_text(encoding="utf-8")
        container = (SOURCE / "program" / "saltynx_state_container.hpp").read_text(encoding="utf-8")

        self.assertIn("133 134 135 136 137", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 137", entry)
        self.assertIn("ISAAC_SC", entry)
        for text in ("isaac-runtime-state.bin", "StateRecord", "BuildStateContainer", "ValidateStateContainer"):
            self.assertIn(text, probe + container)
        self.assertIn("ISMODST1", container)
        self.assertIn("kFormatVersion = 1", container)
        self.assertIn("Checksum", container)

    def test_stage138_reads_the_existing_state_container_without_writing(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        probe_path = SOURCE / "program" / "saltynx_external_plugin_state_persist_probe.cpp"

        self.assertIn("133 134 135 136 137 138", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 138", entry)
        self.assertIn("ISAAC_SX", entry)
        self.assertTrue(probe_path.is_file(), "Stage138 state-container reader is missing")
        probe = probe_path.read_text(encoding="utf-8")
        self.assertIn("isaac-runtime-state.bin", probe)
        self.assertIn("SaltySDCore_fopen", probe)
        self.assertIn("SaltySDCore_fread", probe)
        self.assertIn("SaltySDCore_fclose", probe)
        self.assertNotIn("SaltySDCore_fwrite", probe)

    def test_stage139_has_only_the_core_lookup_abi_and_a_4k_mapping_span(self):
        artifact_value = os.environ.get("ISAAC_SALTYNX_STAGE139_ELF")
        if artifact_value is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE139_ELF to validate a Docker-built Stage139 artifact")

        artifact = Path(artifact_value)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined = {
            name
            for name, binding, section in symbols
            if binding == 1 and section == 0
        }

        self.assertEqual(undefined, {"SaltySDCore_FindSymbol"})
        self.assertEqual(relocations, [("SaltySDCore_FindSymbol", 257)])
        self.assertEqual(self._load_segment_alignments(artifact), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage140_registers_core_file_symbols_in_the_default_runtime_without_io(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        probe_path = SOURCE / "program" / "saltynx_external_plugin_file_api_probe.cpp"

        self.assertIn("133 134 135 136 137 138 139 140", makefile)
        self.assertIn("SALTYNX_STAGE140_CPPFILES", makefile)
        self.assertIn("stage140-combined-deploy", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 140", entry)
        self.assertIn("ISAAC_ST", entry)
        self.assertIn("IsaacModRuntime_RegisterSaltyFileApi", bridge)
        self.assertIn("IsaacModRuntime_SaltyFileApiStatus", bridge)
        self.assertTrue(probe_path.is_file(), "Stage140 file-api registration probe is missing")

        probe = probe_path.read_text(encoding="utf-8")
        for symbol in (
            "SaltySDCore_fopen",
            "SaltySDCore_fread",
            "SaltySDCore_fwrite",
            "SaltySDCore_fclose",
            "IsaacModRuntime_RegisterSaltyFileApi",
            "IsaacModRuntime_SaltyFileApiStatus",
        ):
            self.assertIn(symbol, probe)
        for forbidden in ("fopen(", "fread(", "fwrite(", "fclose("):
            self.assertNotIn(forbidden, probe)

    def test_stage140_has_only_the_core_lookup_abi_and_a_4k_mapping_span(self):
        artifact_value = os.environ.get("ISAAC_SALTYNX_STAGE140_ELF")
        if artifact_value is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE140_ELF to validate a Docker-built Stage140 artifact")

        artifact = Path(artifact_value)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined = {
            name
            for name, binding, section in symbols
            if binding == 1 and section == 0
        }

        self.assertEqual(undefined, {"SaltySDCore_FindSymbol"})
        self.assertEqual(relocations, [("SaltySDCore_FindSymbol", 257)])
        self.assertEqual(self._load_segment_alignments(artifact), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage141_runs_file_io_inside_the_default_runtime(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        program_entry = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        common_makefile = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        probe_path = SOURCE / "program" / "saltynx_external_plugin_runtime_io_probe.cpp"

        self.assertIn("133 134 135 136 137 138 139 140 141", makefile)
        self.assertIn("SALTYNX_STAGE141_CPPFILES", makefile)
        self.assertIn("stage141-combined-deploy", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 141", entry)
        self.assertIn("ISAAC_SU", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 141", program_entry)
        self.assertIn("IsaacModRuntime_SaltyFileIoProbe", bridge)
        self.assertIn("isaac-runtime-runtime-io.bin", bridge)
        io_probe = bridge.split("IsaacModRuntime_SaltyFileIoProbe()", 1)[1]
        self.assertNotIn("IsaacModRuntime_SaltyFileApiStatus()", io_probe)
        self.assertIn("saltynx_external_plugin_runtime_io_probe.cpp", common_makefile)
        self.assertTrue(probe_path.is_file(), "Stage141 Runtime I/O probe is missing")

        probe = probe_path.read_text(encoding="utf-8")
        self.assertIn("IsaacModRuntime_SaltyFileIoProbe", probe)
        for forbidden in (
            "extern \"C\" void* SaltySDCore_fopen",
            "extern \"C\" std::size_t SaltySDCore_fread",
            "extern \"C\" std::size_t SaltySDCore_fwrite",
            "extern \"C\" int SaltySDCore_fclose",
        ):
            self.assertNotIn(forbidden, probe)

    def test_stage141_has_only_the_core_lookup_abi_and_a_4k_mapping_span(self):
        artifact_value = os.environ.get("ISAAC_SALTYNX_STAGE141_ELF")
        if artifact_value is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE141_ELF to validate a Docker-built Stage141 artifact")

        artifact = Path(artifact_value)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined = {
            name
            for name, binding, section in symbols
            if binding == 1 and section == 0
        }

        self.assertEqual(undefined, {"SaltySDCore_FindSymbol"})
        self.assertEqual(relocations, [("SaltySDCore_FindSymbol", 257)])
        self.assertEqual(self._load_segment_alignments(artifact), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage142_runs_the_versioned_state_container_inside_the_default_runtime(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        program_entry = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        common_makefile = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        probe_path = SOURCE / "program" / "saltynx_external_plugin_runtime_state_probe.cpp"

        self.assertIn("133 134 135 136 137 138 139 140 141 142", makefile)
        self.assertIn("SALTYNX_STAGE142_CPPFILES", makefile)
        self.assertIn("stage142-combined-deploy", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 142", entry)
        self.assertIn("ISAAC_SQ", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 142", program_entry)
        self.assertIn("IsaacModRuntime_SaltyStateContainerProbe", bridge)
        self.assertIn("saltynx_state_container.hpp", bridge)
        self.assertIn("isaac-runtime-state.bin", bridge)
        self.assertIn("saltynx_external_plugin_runtime_state_probe.cpp", common_makefile)
        self.assertTrue(probe_path.is_file(), "Stage142 Runtime state-container probe is missing")

        probe = probe_path.read_text(encoding="utf-8")
        self.assertIn("IsaacModRuntime_SaltyStateContainerProbe", probe)
        for forbidden in (
            "extern \"C\" void* SaltySDCore_fopen",
            "extern \"C\" std::size_t SaltySDCore_fread",
            "extern \"C\" std::size_t SaltySDCore_fwrite",
            "extern \"C\" int SaltySDCore_fclose",
        ):
            self.assertNotIn(forbidden, probe)

    def test_stage142_has_only_the_core_lookup_abi_and_a_4k_mapping_span(self):
        artifact_value = os.environ.get("ISAAC_SALTYNX_STAGE142_ELF")
        if artifact_value is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE142_ELF to validate a Docker-built Stage142 artifact")

        artifact = Path(artifact_value)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined = {
            name
            for name, binding, section in symbols
            if binding == 1 and section == 0
        }

        self.assertEqual(undefined, {"SaltySDCore_FindSymbol"})
        self.assertEqual(relocations, [("SaltySDCore_FindSymbol", 257)])
        self.assertEqual(self._load_segment_alignments(artifact), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage143_reads_the_existing_state_container_inside_the_default_runtime(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        bridge = (SOURCE / "saltynx_runtime_bridge.cpp").read_text(encoding="utf-8")
        program_entry = (SOURCE / "program" / "runtime_entry.cpp").read_text(encoding="utf-8")
        common_makefile = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        probe_path = SOURCE / "program" / "saltynx_external_plugin_runtime_state_persist_probe.cpp"

        self.assertIn("133 134 135 136 137 138 139 140 141 142 143", makefile)
        self.assertIn("SALTYNX_STAGE143_CPPFILES", makefile)
        self.assertIn("stage143-combined-deploy", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 143", entry)
        self.assertIn("ISAAC_SY", entry)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 143", program_entry)
        self.assertIn("IsaacModRuntime_SaltyStateContainerPersistProbe", bridge)
        self.assertIn('open(kStateContainerPath, "rb")', bridge)
        persist_probe = bridge.split("IsaacModRuntime_SaltyStateContainerPersistProbe()", 1)[1]
        self.assertNotIn("write(", persist_probe)
        self.assertNotIn("IsaacModRuntime_SaltyFileApiStatus()", persist_probe)
        self.assertIn("saltynx_external_plugin_runtime_state_persist_probe.cpp", common_makefile)
        self.assertTrue(probe_path.is_file(), "Stage143 Runtime state-container reader is missing")

        probe = probe_path.read_text(encoding="utf-8")
        self.assertIn("IsaacModRuntime_SaltyStateContainerPersistProbe", probe)
        for forbidden in (
            "extern \"C\" void* SaltySDCore_fopen",
            "extern \"C\" std::size_t SaltySDCore_fread",
            "extern \"C\" std::size_t SaltySDCore_fwrite",
            "extern \"C\" int SaltySDCore_fclose",
        ):
            self.assertNotIn(forbidden, probe)

    def test_stage143_has_only_the_core_lookup_abi_and_a_4k_mapping_span(self):
        artifact_value = os.environ.get("ISAAC_SALTYNX_STAGE143_ELF")
        if artifact_value is None:
            self.skipTest("set ISAAC_SALTYNX_STAGE143_ELF to validate a Docker-built Stage143 artifact")

        artifact = Path(artifact_value)
        symbols, relocations = self._dynamic_symbols_and_relocations(artifact)
        undefined = {
            name
            for name, binding, section in symbols
            if binding == 1 and section == 0
        }

        self.assertEqual(undefined, {"SaltySDCore_FindSymbol"})
        self.assertEqual(relocations, [("SaltySDCore_FindSymbol", 257)])
        self.assertEqual(self._load_segment_alignments(artifact), [0x1000, 0x1000, 0x1000])
        self.assertEqual(self._saltynx_mapping_size(artifact), 0x3000)

    def test_stage139_probes_the_default_runtime_through_core_symbol_lookup(self):
        makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
        entry = (SOURCE / "saltynx_external_plugin_entry.cpp").read_text(encoding="utf-8")
        bridge_path = SOURCE / "saltynx_runtime_bridge.cpp"
        probe_path = SOURCE / "program" / "saltynx_external_plugin_bridge_probe.cpp"

        self.assertIn("133 134 135 136 137 138 139", makefile)
        self.assertIn("stage139-combined-deploy", makefile)
        self.assertIn("deploy-saltynx/stage139-combined", makefile)
        self.assertIn("env -u MAKELEVEL $(MAKE)", makefile)
        self.assertIn("SKIP_DEFAULT_PC_MODS=1", makefile)
        self.assertIn("EXL_DIAGNOSTIC_STAGE == 139", entry)
        self.assertIn("ISAAC_SB", entry)
        self.assertTrue(bridge_path.is_file(), "default Runtime bridge export is missing")
        self.assertTrue(probe_path.is_file(), "Stage139 bridge probe is missing")

        bridge = bridge_path.read_text(encoding="utf-8")
        probe = probe_path.read_text(encoding="utf-8")
        common_makefile = (ROOT / "misc" / "mk" / "common.mk").read_text(encoding="utf-8")
        vpath_start = common_makefile.index("export VPATH")
        vpath_end = common_makefile.index("export DEPSDIR", vpath_start)
        vpath = common_makefile[vpath_start:vpath_end]
        self.assertIn("IsaacModRuntime_SaltyBridgeProbe", bridge)
        self.assertIn("visibility(\"default\")", bridge)
        self.assertIn("CPPFILES += saltynx_runtime_bridge.cpp", common_makefile)
        self.assertLess(vpath.index("$(foreach dir,$(SOURCES)"), vpath.index("$(ROOT_SOURCE)"))
        self.assertIn("$(EXL_ARTIFACT_DIR)/$(TARGET).elf", common_makefile)
        self.assertIn("SaltySDCore_FindSymbol", probe)
        self.assertIn("IsaacModRuntime_SaltyBridgeProbe", probe)
        for forbidden in ("SaltySDCore_fopen", "SaltySDCore_fread", "SaltySDCore_fwrite"):
            self.assertNotIn(forbidden, probe)


if __name__ == "__main__":
    unittest.main()
