.section ".text.crt0", "ax"

.global __module_start
__module_start:
    b exl_main
    .word __nx_mod0 - __module_start

    .align 4
    .ascii "~~exlaunch uwu~~"

.section ".rodata.mod0", "a"

.align 2
__nx_mod0:
    .ascii "MOD0"
    .word __dynamic_start__ - __nx_mod0
    .word __bss_start__ - __nx_mod0
    .word __bss_end__ - __nx_mod0
    .word 0
    .word 0
    .word 0
