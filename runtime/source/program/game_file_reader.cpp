#if defined(EXL_DIAGNOSTIC_STAGE) && \
    (EXL_DIAGNOSTIC_STAGE == 11 || EXL_DIAGNOSTIC_STAGE == 12 || EXL_DIAGNOSTIC_STAGE == 118)
#include "../game_file_reader.cpp"
#elif !defined(EXL_DIAGNOSTIC_STAGE) || EXL_DIAGNOSTIC_STAGE == 13
#include "../game_file_reader.cpp"
#endif
