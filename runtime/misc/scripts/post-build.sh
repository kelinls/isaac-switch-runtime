#!/bin/bash
set -e

OUT_NSO="${OUT}/atmosphere/contents/${PROGRAM_ID}/exefs/${BINARY_NAME}"

rm -rf -- "${OUT}"

if [ "${PERSISTENCE_TRACE:-0}" = "1" ]; then
    NM_TOOL="${NM:-${DEVKITPRO}/devkitA64/bin/aarch64-none-elf-nm}"
    if ! command -v "${NM_TOOL}" >/dev/null 2>&1; then
        echo "post-build: nm tool not found: ${NM_TOOL}" >&2
        exit 1
    fi
    UNRESOLVED_TRACE_SYMBOLS="$("${NM_TOOL}" -C -u "${EXL_ARTIFACT_DIR}/${NAME}.elf" | grep -E 'PersistenceTrace::|ManagerUpdateHookAudit::' || true)"
    if [ -n "${UNRESOLVED_TRACE_SYMBOLS}" ]; then
        echo "post-build: runtime contains unresolved PersistenceTrace symbols:" >&2
        echo "${UNRESOLVED_TRACE_SYMBOLS}" >&2
        exit 1
    fi
fi

if [ -n "${SALTYNX_PLUGIN_OUTPUT:-}" ]; then
    # Gate: SaltyNX applies a plugin's symbol relocations but not
    # R_AARCH64_RELATIVE, so any pointer that lives in `.data` (a C++ vtable is the
    # usual cause) keeps its raw on-disk value and the plugin branches to a
    # near-null address on first use. Build `20260910670000` shipped exactly that
    # and died on hardware with `Result 0x2A8 ... PC=0x60`. Refuse to publish such a
    # plugin rather than discover it as a crash on the console.
    PLUGIN_CHECK="$(cd "$(dirname "$0")" && pwd)/../../../tools/check_saltynx_plugin_elf.py"
    if [ ! -f "${PLUGIN_CHECK}" ]; then
        echo "post-build: plugin ELF gate is missing: ${PLUGIN_CHECK}" >&2
        exit 1
    fi
    if ! "${PYTHON:-python3}" "${PLUGIN_CHECK}" "${EXL_ARTIFACT_DIR}/${NAME}.elf"; then
        echo "post-build: refusing to deploy a plugin SaltyNX cannot load" >&2
        exit 1
    fi

    mkdir -p "$(dirname "${SALTYNX_PLUGIN_OUTPUT}")"
    cp -- "${EXL_ARTIFACT_DIR}/${NAME}.elf" "${SALTYNX_PLUGIN_OUTPUT}"
    if [ -n "${SALTYNX_STAGE134_READ_INPUT:-}" ]; then
        cp -- "${SALTYNX_STAGE134_READ_INPUT}" "$(dirname "${SALTYNX_PLUGIN_OUTPUT}")/isaac-runtime-read.bin"
    fi
    rm -f -- "${EXL_ARTIFACT_DIR}/${NAME}.nso" "${EXL_ARTIFACT_DIR}/${NAME}.npdm"
    exit 0
fi

# Create out directory.
mkdir -p "$(dirname "${OUT_NSO}")"

# Copy build into out
mv -- "${EXL_ARTIFACT_DIR}/${NAME}.nso" "${OUT_NSO}"
rm -f -- "${EXL_ARTIFACT_DIR}/${NAME}.npdm"

# Copy ELF to user path if defined.
if [ -n "${ELF_EXTRACT:-}" ]; then
    cp -- "${EXL_ARTIFACT_DIR}/${NAME}.elf" "${ELF_EXTRACT}"
fi
