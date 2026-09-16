game_file_reader.o: ../source/program/game_file_reader.cpp \
 ../source/program/../game_file_reader.cpp \
 ../source/program/../game_file_reader.hpp \
 ../source/program/../module_finder.hpp \
 ../source/program/../runtime_constants.hpp ../source/program/../lib.hpp \
 ../source/program/../common.hpp ../source/program/../types.h \
 ../source/program/../lib/alloc.hpp ../source/program/../lib/nx/nx.h \
 ../source/program/../lib/nx/result.h ../source/program/../lib/nx/types.h \
 ../source/program/../lib/nx/smc.h \
 ../source/program/../lib/nx/kernel/svc.h \
 ../source/program/../lib/nx/kernel/../arm/thread_context.h \
 ../source/program/../lib/nx/arm/cache.h \
 ../source/program/../lib/nx/arm/tls.h \
 ../source/program/../lib/nx/kernel/virtmem.h \
 ../source/program/../lib/result.hpp ../source/lib/diag/assert.hpp \
 ../source/program/setting.hpp ../source/program/../lib/libsetting.hpp \
 ../source/program/../lib/armv8.hpp \
 ../source/program/../lib/util/math/bitset.hpp \
 ../source/program/../lib/armv8/register.hpp \
 ../source/program/../lib/armv8/instructions.hpp \
 ../source/program/../lib/armv8/instructions/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/add_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/adds_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/sub_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/subs_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/cmn_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/cmp_immediate.hpp \
 ../source/program/../lib/armv8/instructions/op100x/logical_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movk.hpp \
 ../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movn.hpp \
 ../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movz.hpp \
 ../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/base.hpp \
 ../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/adr.hpp \
 ../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/adrp.hpp \
 ../source/program/../lib/armv8/instructions/op101x/base.hpp \
 ../source/program/../lib/armv8/instructions/op101x/hints/base.hpp \
 ../source/program/../lib/armv8/instructions/op101x/hints/nop.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/b.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/bl.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/base.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/br.hpp \
 ../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/ret.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/base.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_register_literal/base.hpp \
 ../source/lib/util/math/sign_extend.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_register_literal/ldr_literal.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/base.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/ldr_register_offset.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/str_register_offset.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/ldur_unscaled_immediate.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/stur_unscaled_immediate.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/base.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/ldr_register_immediate.hpp \
 ../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/str_register_immediate.hpp \
 ../source/program/../lib/armv8/instructions/opx101/base.hpp \
 ../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/base.hpp \
 ../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/orr_shifted_register.hpp \
 ../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/mov_register.hpp \
 ../source/program/../lib/diag/abort.hpp \
 ../source/program/../lib/log/ilogger.hpp \
 ../source/program/../lib/log/svc_logger.hpp \
 ../source/program/../lib/patch/code_patcher.hpp \
 ../source/program/../lib/patch/stream_patcher.hpp \
 ../source/program/../lib/patch/patcher_impl.hpp \
 ../source/lib/util/sys/rw_pages.hpp \
 ../source/lib/util/sys/mem_layout.hpp \
 ../source/lib/util/module_index.hpp ../source/rtld.hpp \
 ../source/rtld/ModuleHeader.hpp ../source/rtld/ModuleObject.hpp \
 ../source/rtld/ModuleList.hpp ../source/lib/util/sys/module_info.hpp \
 ../source/lib/util/sys/mod0.hpp ../source/lib/util/sys/range.hpp \
 ../source/lib/util/sys/modules.hpp ../source/lib/util/typed_storage.hpp \
 ../source/lib/util/aligned_storage.hpp \
 ../source/program/../lib/patch/random_access_patcher.hpp \
 ../source/program/../lib/util/sys/cur_proc_handle.hpp \
 ../source/program/../lib/util/sys/jit.hpp \
 ../source/program/../lib/util/sys/soc.hpp \
 ../source/program/../lib/util/crc32.hpp \
 ../source/program/../lib/util/modules.hpp \
 ../source/program/../lib/util/murmur3.hpp \
 ../source/program/../lib/util/ptr_path.hpp \
 ../source/program/../lib/util/random.hpp \
 ../source/program/../lib/util/stack_trace.hpp \
 ../source/program/../lib/util/strings.hpp \
 ../source/program/../lib/util/version.hpp ../source/program/version.hpp \
 ../source/program/../lib/reloc/reloc.hpp \
 ../source/program/../lib/reloc/table/lookup_entry.hpp \
 ../source/program/../lib/reloc/table/lookup.hpp \
 ../source/program/../lib/reloc/table/table.hpp \
 ../source/program/../lib/reloc/table/table_set.hpp \
 ../source/program/../lib/hook/base.hpp ../source/lib/util/func_ptrs.hpp \
 ../source/lib/util/type_traits.hpp ../source/lib/log/logger_mgr.hpp \
 ../source/program/loggers.hpp \
 ../source/program/../lib/hook/nx64/impl.hpp \
 ../source/program/../lib/hook/nx64/inline_impl.hpp \
 ../source/program/../lib/hook/nx64/../../util/neon.hpp \
 ../source/program/../lib/hook/class.hpp \
 ../source/program/../lib/hook/deprecated.hpp \
 ../source/program/../lib/hook/inline.hpp \
 ../source/program/../lib/hook/replace.hpp \
 ../source/program/../lib/hook/trampoline.hpp
../source/program/../game_file_reader.cpp:
../source/program/../game_file_reader.hpp:
../source/program/../module_finder.hpp:
../source/program/../runtime_constants.hpp:
../source/program/../lib.hpp:
../source/program/../common.hpp:
../source/program/../types.h:
../source/program/../lib/alloc.hpp:
../source/program/../lib/nx/nx.h:
../source/program/../lib/nx/result.h:
../source/program/../lib/nx/types.h:
../source/program/../lib/nx/smc.h:
../source/program/../lib/nx/kernel/svc.h:
../source/program/../lib/nx/kernel/../arm/thread_context.h:
../source/program/../lib/nx/arm/cache.h:
../source/program/../lib/nx/arm/tls.h:
../source/program/../lib/nx/kernel/virtmem.h:
../source/program/../lib/result.hpp:
../source/lib/diag/assert.hpp:
../source/program/setting.hpp:
../source/program/../lib/libsetting.hpp:
../source/program/../lib/armv8.hpp:
../source/program/../lib/util/math/bitset.hpp:
../source/program/../lib/armv8/register.hpp:
../source/program/../lib/armv8/instructions.hpp:
../source/program/../lib/armv8/instructions/base.hpp:
../source/program/../lib/armv8/instructions/op100x/base.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/base.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/add_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/adds_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/sub_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/subs_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/cmn_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/add_subtract_immediate/cmp_immediate.hpp:
../source/program/../lib/armv8/instructions/op100x/logical_immediate/base.hpp:
../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/base.hpp:
../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movk.hpp:
../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movn.hpp:
../source/program/../lib/armv8/instructions/op100x/move_wide_immediate/movz.hpp:
../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/base.hpp:
../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/adr.hpp:
../source/program/../lib/armv8/instructions/op100x/pc_rel_addressing/adrp.hpp:
../source/program/../lib/armv8/instructions/op101x/base.hpp:
../source/program/../lib/armv8/instructions/op101x/hints/base.hpp:
../source/program/../lib/armv8/instructions/op101x/hints/nop.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/base.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/b.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_immediate/bl.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/base.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/br.hpp:
../source/program/../lib/armv8/instructions/op101x/unconditional_branch_register/ret.hpp:
../source/program/../lib/armv8/instructions/opx1x0/base.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_register_literal/base.hpp:
../source/lib/util/math/sign_extend.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_register_literal/ldr_literal.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/base.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/ldr_register_offset.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_offset/str_register_offset.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/base.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/ldur_unscaled_immediate.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unscaled_immediate/stur_unscaled_immediate.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/base.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/ldr_register_immediate.hpp:
../source/program/../lib/armv8/instructions/opx1x0/load_store_register_unsigned_immediate/str_register_immediate.hpp:
../source/program/../lib/armv8/instructions/opx101/base.hpp:
../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/base.hpp:
../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/orr_shifted_register.hpp:
../source/program/../lib/armv8/instructions/opx101/logical_shifted_register/mov_register.hpp:
../source/program/../lib/diag/abort.hpp:
../source/program/../lib/log/ilogger.hpp:
../source/program/../lib/log/svc_logger.hpp:
../source/program/../lib/patch/code_patcher.hpp:
../source/program/../lib/patch/stream_patcher.hpp:
../source/program/../lib/patch/patcher_impl.hpp:
../source/lib/util/sys/rw_pages.hpp:
../source/lib/util/sys/mem_layout.hpp:
../source/lib/util/module_index.hpp:
../source/rtld.hpp:
../source/rtld/ModuleHeader.hpp:
../source/rtld/ModuleObject.hpp:
../source/rtld/ModuleList.hpp:
../source/lib/util/sys/module_info.hpp:
../source/lib/util/sys/mod0.hpp:
../source/lib/util/sys/range.hpp:
../source/lib/util/sys/modules.hpp:
../source/lib/util/typed_storage.hpp:
../source/lib/util/aligned_storage.hpp:
../source/program/../lib/patch/random_access_patcher.hpp:
../source/program/../lib/util/sys/cur_proc_handle.hpp:
../source/program/../lib/util/sys/jit.hpp:
../source/program/../lib/util/sys/soc.hpp:
../source/program/../lib/util/crc32.hpp:
../source/program/../lib/util/modules.hpp:
../source/program/../lib/util/murmur3.hpp:
../source/program/../lib/util/ptr_path.hpp:
../source/program/../lib/util/random.hpp:
../source/program/../lib/util/stack_trace.hpp:
../source/program/../lib/util/strings.hpp:
../source/program/../lib/util/version.hpp:
../source/program/version.hpp:
../source/program/../lib/reloc/reloc.hpp:
../source/program/../lib/reloc/table/lookup_entry.hpp:
../source/program/../lib/reloc/table/lookup.hpp:
../source/program/../lib/reloc/table/table.hpp:
../source/program/../lib/reloc/table/table_set.hpp:
../source/program/../lib/hook/base.hpp:
../source/lib/util/func_ptrs.hpp:
../source/lib/util/type_traits.hpp:
../source/lib/log/logger_mgr.hpp:
../source/program/loggers.hpp:
../source/program/../lib/hook/nx64/impl.hpp:
../source/program/../lib/hook/nx64/inline_impl.hpp:
../source/program/../lib/hook/nx64/../../util/neon.hpp:
../source/program/../lib/hook/class.hpp:
../source/program/../lib/hook/deprecated.hpp:
../source/program/../lib/hook/inline.hpp:
../source/program/../lib/hook/replace.hpp:
../source/program/../lib/hook/trampoline.hpp:
