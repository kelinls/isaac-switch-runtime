#include "fs_ipc.h"

#include <switch/services/fs.h>
#include <switch/services/sm.h>

static FsFileSystem g_FileSystem;
static FsFile g_File;
static bool g_SmInitialized;
static bool g_FsInitialized;
static bool g_FileSystemOpen;
static bool g_FileOpen;

static RuntimeFsDiagnosticResult MakeDiagnosticResult(RuntimeFsDiagnosticStage stage, Result result) {
    return (RuntimeFsDiagnosticResult){
        .stage = (uint32_t)stage,
        .result = (uint32_t)result,
    };
}

void RuntimeFsLogClose(void) {
    if (g_FileOpen) {
        fsFileClose(&g_File);
        g_File = (FsFile){};
        g_FileOpen = false;
    }
    if (g_FileSystemOpen) {
        fsFsClose(&g_FileSystem);
        g_FileSystem = (FsFileSystem){};
        g_FileSystemOpen = false;
    }
    if (g_FsInitialized) {
        fsExit();
        g_FsInitialized = false;
    }
    if (g_SmInitialized) {
        smExit();
        g_SmInitialized = false;
    }
}

int RuntimeFsLogOpen(const char* directory, const char* path, int64_t* writeOffset) {
    if (directory == NULL || path == NULL || writeOffset == NULL) {
        return 0;
    }
    if (g_FileOpen) {
        return R_SUCCEEDED(fsFileGetSize(&g_File, writeOffset));
    }
    if (!g_SmInitialized) {
        if (R_FAILED(smInitialize())) {
            return 0;
        }
        g_SmInitialized = true;
    }
    if (!g_FsInitialized) {
        if (R_FAILED(fsInitialize())) {
            RuntimeFsLogClose();
            return 0;
        }
        g_FsInitialized = true;
    }
    if (!g_FileSystemOpen) {
        if (R_FAILED(fsOpenSdCardFileSystem(&g_FileSystem))) {
            RuntimeFsLogClose();
            return 0;
        }
        g_FileSystemOpen = true;
    }

    fsFsCreateDirectory(&g_FileSystem, directory);
    fsFsCreateFile(&g_FileSystem, path, 0, 0);
    if (R_FAILED(fsFsOpenFile(&g_FileSystem, path,
                              FsOpenMode_Write | FsOpenMode_Append, &g_File))) {
        RuntimeFsLogClose();
        return 0;
    }
    g_FileOpen = true;
    if (R_FAILED(fsFileGetSize(&g_File, writeOffset))) {
        RuntimeFsLogClose();
        return 0;
    }
    return 1;
}

int RuntimeFsLogWrite(int64_t writeOffset, const void* data, uint64_t size) {
    return g_FileOpen && data != NULL &&
           R_SUCCEEDED(fsFileWrite(&g_File, writeOffset, data, size, FsWriteOption_Flush));
}

RuntimeFsDiagnosticResult RuntimeFsLogDiagnose(const char* directory, const char* path) {
    static const char kDiagnosticLine[] = "event=fs_diagnostic status=success\n";
    s64 writeOffset = 0;
    Result result = 0;

    RuntimeFsLogClose();
    if (directory == NULL || path == NULL) {
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_InvalidArguments, 0);
    }
    if (R_FAILED(result = smInitialize())) {
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_ServiceManager, result);
    }
    g_SmInitialized = true;
    if (R_FAILED(result = fsInitialize())) {
        RuntimeFsLogClose();
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_FsInitialize, result);
    }
    g_FsInitialized = true;
    if (R_FAILED(result = fsOpenSdCardFileSystem(&g_FileSystem))) {
        RuntimeFsLogClose();
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_OpenSdCardFileSystem, result);
    }
    g_FileSystemOpen = true;

    // These may report an existing path; opening the file is the decisive check.
    fsFsCreateDirectory(&g_FileSystem, directory);
    fsFsCreateFile(&g_FileSystem, path, 0, 0);
    if (R_FAILED(result = fsFsOpenFile(&g_FileSystem, path,
                                       FsOpenMode_Write | FsOpenMode_Append, &g_File))) {
        RuntimeFsLogClose();
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_OpenFile, result);
    }
    g_FileOpen = true;
    if (R_FAILED(result = fsFileGetSize(&g_File, &writeOffset))) {
        RuntimeFsLogClose();
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_GetFileSize, result);
    }
    if (R_FAILED(result = fsFileWrite(&g_File, writeOffset, kDiagnosticLine,
                                      sizeof(kDiagnosticLine) - 1, FsWriteOption_Flush))) {
        RuntimeFsLogClose();
        return MakeDiagnosticResult(RuntimeFsDiagnosticStage_WriteFile, result);
    }
    RuntimeFsLogClose();
    return MakeDiagnosticResult(RuntimeFsDiagnosticStage_Success, 0);
}
