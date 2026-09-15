#pragma once

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    RuntimeFsDiagnosticStage_InvalidArguments = 1,
    RuntimeFsDiagnosticStage_ServiceManager = 2,
    RuntimeFsDiagnosticStage_FsInitialize = 3,
    RuntimeFsDiagnosticStage_OpenSdCardFileSystem = 4,
    RuntimeFsDiagnosticStage_OpenFile = 5,
    RuntimeFsDiagnosticStage_GetFileSize = 6,
    RuntimeFsDiagnosticStage_WriteFile = 7,
    RuntimeFsDiagnosticStage_Success = 8,
} RuntimeFsDiagnosticStage;

typedef struct {
    uint32_t stage;
    uint32_t result;
} RuntimeFsDiagnosticResult;

int RuntimeFsLogOpen(const char* directory, const char* path, int64_t* writeOffset);
int RuntimeFsLogWrite(int64_t writeOffset, const void* data, uint64_t size);
void RuntimeFsLogClose(void);
RuntimeFsDiagnosticResult RuntimeFsLogDiagnose(const char* directory, const char* path);

#ifdef __cplusplus
}
#endif
