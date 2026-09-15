#!/bin/sh
# Force a full rebuild when the compiler flags change.
#
# `TEST_BUILD_ID` reaches every translation unit as `-DEXL_TEST_BUILD_ID=...`, but
# make only compares timestamps: on 2026-09-11 an incremental module build kept
# `test_run_observer.o` from an earlier build, so the deployed module stamped
# Build ID 20260910740000 into its snapshot while the package manifest claimed
# 20260910820000. The artifact was a mix of two builds and its own records
# disagreed with the manifest, which is exactly the kind of evidence a diagnosis
# cannot afford.
#
# The fix is to remember the flag string that produced the objects in the build
# directory and to drop those objects when it differs. Anything that changes the
# compiler command line -- the probe macros, the trace switches, the build id --
# therefore forces a rebuild, and the stamp is written after the objects are gone
# so an interrupted build cannot leave a stale stamp behind.
#
# Usage: check-build-id.sh <build-dir> <flags-string>
set -e

BUILD_DIR="$1"
FLAGS="$2"

if [ -z "${BUILD_DIR}" ] || [ -z "${FLAGS}" ]; then
    echo "check-build-id: usage: check-build-id.sh <build-dir> <flags-string>" >&2
    exit 2
fi

STAMP="${BUILD_DIR}/.build_flags"
mkdir -p "${BUILD_DIR}"

if [ -f "${STAMP}" ] && [ "$(cat "${STAMP}")" = "${FLAGS}" ]; then
    exit 0
fi

if [ -f "${STAMP}" ]; then
    echo "build flags changed -- rebuilding every object in ${BUILD_DIR}"
else
    echo "no build stamp in ${BUILD_DIR} -- rebuilding every object once"
    # A build directory from before this check has objects of unknown provenance:
    # its stamp is missing, and the flags that produced it cannot be recovered.
    :
fi

rm -f "${BUILD_DIR}"/*.o "${BUILD_DIR}"/*.d 2>/dev/null || true
printf '%s' "${FLAGS}" > "${STAMP}"
