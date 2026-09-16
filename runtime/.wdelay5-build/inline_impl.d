inline_impl.o: ../source/lib/hook/nx64/inline_impl.cpp \
 ../source/common.hpp ../source/types.h ../source/lib/alloc.hpp \
 ../source/lib/nx/nx.h ../source/lib/nx/result.h ../source/lib/nx/types.h \
 ../source/lib/nx/smc.h ../source/lib/nx/kernel/svc.h \
 ../source/lib/nx/kernel/../arm/thread_context.h \
 ../source/lib/nx/arm/cache.h ../source/lib/nx/arm/tls.h \
 ../source/lib/nx/kernel/virtmem.h ../source/lib/result.hpp \
 ../source/lib/diag/assert.hpp ../source/program/setting.hpp \
 ../source/lib/libsetting.hpp \
 ../source/lib/hook/nx64/../../util/sys/jit.hpp \
 ../source/lib/util/typed_storage.hpp \
 ../source/lib/util/aligned_storage.hpp \
 ../source/lib/hook/nx64/../../util/sys/rw_pages.hpp \
 ../source/lib/hook/nx64/../../armv8.hpp \
 ../source/lib/hook/nx64/../../util/math/bitset.hpp \
 ../source/lib/hook/nx64/../../armv8/register.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/add_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/adds_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/sub_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/subs_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/cmn_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/cmp_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/logical_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movk.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movn.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movz.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/adr.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/adrp.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/hints/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/hints/nop.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/b.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/bl.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/br.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/ret.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_register_literal/base.hpp \
 ../source/lib/util/math/sign_extend.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_register_literal/ldr_literal.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/ldr_register_offset.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/str_register_offset.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/ldur_unscaled_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/stur_unscaled_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/ldr_register_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/str_register_immediate.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx101/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/base.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/orr_shifted_register.hpp \
 ../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/mov_register.hpp \
 ../source/lib/hook/nx64/impl.hpp ../source/lib/hook/nx64/inline_impl.hpp \
 ../source/lib/hook/nx64/../../util/neon.hpp
../source/common.hpp:
../source/types.h:
../source/lib/alloc.hpp:
../source/lib/nx/nx.h:
../source/lib/nx/result.h:
../source/lib/nx/types.h:
../source/lib/nx/smc.h:
../source/lib/nx/kernel/svc.h:
../source/lib/nx/kernel/../arm/thread_context.h:
../source/lib/nx/arm/cache.h:
../source/lib/nx/arm/tls.h:
../source/lib/nx/kernel/virtmem.h:
../source/lib/result.hpp:
../source/lib/diag/assert.hpp:
../source/program/setting.hpp:
../source/lib/libsetting.hpp:
../source/lib/hook/nx64/../../util/sys/jit.hpp:
../source/lib/util/typed_storage.hpp:
../source/lib/util/aligned_storage.hpp:
../source/lib/hook/nx64/../../util/sys/rw_pages.hpp:
../source/lib/hook/nx64/../../armv8.hpp:
../source/lib/hook/nx64/../../util/math/bitset.hpp:
../source/lib/hook/nx64/../../armv8/register.hpp:
../source/lib/hook/nx64/../../armv8/instructions.hpp:
../source/lib/hook/nx64/../../armv8/instructions/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/add_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/adds_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/sub_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/subs_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/cmn_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/add_subtract_immediate/cmp_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/logical_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movk.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movn.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/move_wide_immediate/movz.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/adr.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op100x/pc_rel_addressing/adrp.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/hints/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/hints/nop.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/b.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_immediate/bl.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/br.hpp:
../source/lib/hook/nx64/../../armv8/instructions/op101x/unconditional_branch_register/ret.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_register_literal/base.hpp:
../source/lib/util/math/sign_extend.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_register_literal/ldr_literal.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/ldr_register_offset.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_offset/str_register_offset.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/ldur_unscaled_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unscaled_immediate/stur_unscaled_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/ldr_register_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx1x0/load_store_register_unsigned_immediate/str_register_immediate.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx101/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/base.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/orr_shifted_register.hpp:
../source/lib/hook/nx64/../../armv8/instructions/opx101/logical_shifted_register/mov_register.hpp:
../source/lib/hook/nx64/impl.hpp:
../source/lib/hook/nx64/inline_impl.hpp:
../source/lib/hook/nx64/../../util/neon.hpp:
