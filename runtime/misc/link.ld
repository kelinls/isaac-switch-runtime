OUTPUT_FORMAT(elf64-littleaarch64)
OUTPUT_ARCH(aarch64)
ENTRY(__module_start)

PHDRS
{
  text PT_LOAD FLAGS(5);
  rodata PT_LOAD FLAGS(4);
  data PT_LOAD FLAGS(6);
  dynamic PT_DYNAMIC;
}

SECTIONS
{
  PROVIDE_HIDDEN(__start__ = 0x0);
  . = __start__;
  __code_start__ = .;

  /* App code */
  .text : {
    HIDDEN(__text_start__ = .);
    KEEP (*(.text.crt0))
    *(.text .text.*)
    HIDDEN(__text_end__ = .);
  } :text

  /* Trampoline and stuffs */
  .plt : { *(.plt .plt.*) } :text
  __code_end__ = .;

  /* Read-only sections */
  ASSERT(!DEFINED(EXL_STARTUP_PROBE_COMPAT_LAYOUT) || __code_end__ < 0x55000,
         "Stage 6 compatibility layout requires RX below 0x55000")
  /*
   * 段地址按体积推导：只读段紧接代码段，读写段紧随只读段，中间不留未映射页。
   * exlaunch 的 FindModules() 走 svcQueryMemory，靠 CodeStatic+Rx ->
   * CodeStatic+R -> CodeMutable+Rw 这个连续三元组识别模块；中间插入未映射页会让
   * 状态机复位、Runtime 找不到自己，mem_layout 断言中止（2026-09-10 真机 Data Abort，
   * "ModuleIdx < ModuleIndex::End"）。硬件要求只有这一条，加上“只读段非空”。
   *
   * 2026-09-12 复核：原来的 `--defsym=EXL_LAYOUT_PIN=0x54000` 把某次真机验证过的
   * **历史地址**（代码 0x54000、只读段结束 0x75000）当成了上限断言。自 806adbd 起段
   * 地址已改为按体积推导，这两个常数不再是放置指令，只剩“涨过就链接失败”的闸门作用，
   * 会在功能没写满之前先把构建拦住（探针构建曾真实撞线，见 misc/mk/common.mk）。
   * 因此这里换成两条**失控增长**闸门：单段 512 KiB / 只读段结束 1 MiB，比实测用量留出
   * 一倍以上余量；实际用量与余量由 `tools/runtime_layout_budget.py` 在每次构建后报告。
   */
  ASSERT(__code_end__ <= 0x80000,
         "RX 段超过 512 KiB：先确认是否为失控增长，再按预算规则调整上限")
  . = DEFINED(EXL_STARTUP_PROBE_COMPAT_LAYOUT) ? 0x55000 : ALIGN(0x1000);

  /* App name */
  .module_name : { KEEP (*(.nx-module-name)) } :rodata

  /* Make sure everything is aligned */
  . = ALIGN(8);

  /* App rodata */
  .layout_padding : { KEEP(*(.rodata.layout_padding)) } :rodata
  .rodata : {
    *(.rodata .rodata.*)
  } :rodata

  /* All the symbols needed for relocation lookup */
  .hash     : { *(.hash) } :rodata
  .gnu.hash : { *(.gnu.hash) } :rodata
  .dynsym   : { *(.dynsym .dynsym.*) } :rodata
  .dynstr   : { *(.dynstr .dynstr.*) } :rodata

  __rel_dyn_start__ = .;
  .rel.dyn : { *(.rel.dyn) } :rodata
  __rel_dyn_end__ = .;

  __rela_dyn_start__ = .;
  .rela.dyn : { *(.rela.dyn) } :rodata
  __rela_dyn_end__ = .;

  __rel_plt_start__ = .;
  .rel.plt        : { *(.rel.plt) } :rodata
  __rel_plt_end__ = .;

  __rela_plt_start__ = .;
  .rela.plt       : { *(.rela.plt) } :rodata
  __rela_plt_end__ = .;

  /* All exception handling sections */
  .gcc_except_table : { *(.gcc_except_table .gcc_except_table.*) } :rodata

  /*
   * 只读段预算：`crt0.s` 的 MOD0 头要求 `__eh_frame_hdr_start__/end__` 两个符号，
   * 但本模块不抛异常、也不做栈回溯（崩溃定位靠“模块名 + 偏移”）。原先 `.eh_frame`
   * 输出段用 `KEEP` 把链接进来的所有对象的栈回溯表全部保留（实测 36.5 KB，占只读段近 8%）。这两个符号保留为同一个地址（长度 0），
   * MOD0 里的 eh_frame_hdr 就是空区间，加载器自然不会注册任何展开表；`.eh_frame`
   * 本体直接丢弃。
   */
  HIDDEN(__eh_frame_hdr_start__ = .);
  HIDDEN(__eh_frame_hdr_end__ = .);

  /* Misc .rodata stuffs (build-id, etc.) */
  .note.gnu.build-id : { *(.note.gnu.build-id) } :rodata

  /* Read-write sections */
  ASSERT(. > __code_end__, "只读段必须非空：exlaunch 靠 Rx->R->Rw 三元组识别模块")
  ASSERT(. <= 0x100000,
         "只读段结束超过 1 MiB：先确认是否为失控增长，再按预算规则调整上限")
  . = DEFINED(EXL_STARTUP_PROBE_COMPAT_LAYOUT) ? 0x76000 : ALIGN(0x1000);
  ASSERT((. & 0xFFF) == 0,
         "读写段起点必须 4 KiB 对齐：SaltyNX 1.8.1 用 PT_DYNAMIC 计算插件映射跨度")

  /*
   * SaltyNX 1.8.1 includes PT_DYNAMIC when calculating an external
   * plugin's mapping span. Keep it at the start of this page so that span
   * remains page-aligned even for a minimal plugin with only a small GOT.
   */
  .dynamic : {
    HIDDEN(__dynamic_start__ = .);
    *(.dynamic)
  } :data :dynamic

  /* App data */
  .data : {
    *(.data .data.*)
  } :data

  /* This section should be made read only after relocation but in practice we will not do that */
  .data.rela.ro : {
    *(.data.rela.ro.local*)
    *(.data.rela.ro .data.rela.ro.*)
  } :data

  /* This section should be made read only after relocation but in practice we will not do that */
  .data.rel.ro : {
    *(.data.rel.ro.local*)
    *(.data.rel.ro .data.rel.ro.*)
  } :data

  /* All GOT sections */
  __got_start__ = .;
  .got : { *(.got.plt) *(.igot.plt) *(.got) *(.igot) } :data
  __got_end__ = .;

  /* Align for .init_array/.fini_array */
  . = ALIGN(8);

  .preinit_array ALIGN(8) :
  {
    PROVIDE_HIDDEN (__preinit_array_start = .);
    KEEP (*(.preinit_array))
    PROVIDE_HIDDEN (__preinit_array_end = .);
  } :data

  /* App init array */
  .init_array : {
    PROVIDE_HIDDEN (__init_array_start = .);
    KEEP (*(SORT_BY_INIT_PRIORITY(.init_array.*)))
    KEEP (*(.init_array))
    PROVIDE_HIDDEN (__init_array_end  = .);
  } :data

  /* App fini array */
  .fini_array : {
    PROVIDE_HIDDEN (__fini_array_start__ = .);
    KEEP (*(SORT_BY_INIT_PRIORITY(.fini_array.*)))
    KEEP (*(.fini_array))
    PROVIDE_HIDDEN (__fini_array_end__ = .);
  } :data

  /* Thread Local sections */

  .tdata : {
    __tdata_align_abs__ = ABSOLUTE(.);
    __tdata_start__ = .;
    *(.tdata .tdata.*)
    __tdata_end__ = .;
  } :data

  .tbss : {
    __tbss_align_abs__ = ABSOLUTE(.);
    __tbss_start__ = .;
    *(.tbss .tbss.*)
    *(.tcommon)
    __tbss_end__ = .;
  } :data

  /* BSS section */
  . = ALIGN(0x1000);

  .bss : {
    HIDDEN(__bss_start__ = .);
    *(.bss .bss.*)
    *(COMMON)
    . = ALIGN(8);
    HIDDEN(__bss_end__ = .);
    . = ALIGN(0x1000);
  } :data

  __end__ = ABSOLUTE(.);

  HIDDEN(__argdata__ = .);

  /DISCARD/ : {
    /* No need of the interpreter */
    *(.interp)
    /* 栈回溯表：见上面对 __eh_frame_hdr_start__ 的说明 */
    *(.eh_frame .eh_frame.*)
    *(.eh_frame_hdr .eh_frame_hdr.*)
  }
}
