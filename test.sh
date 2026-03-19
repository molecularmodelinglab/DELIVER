#!/usr/bin/env bash
# Run all DELIVER tests.
#
# Usage:
#   bash test.sh            # all tests
#   bash test.sh --nf       # Nextflow stub tests only
#   bash test.sh --py       # Python unit tests only

set -euo pipefail

DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

RUN_NF=true
RUN_PY=true

if [[ "${1:-}" == "--nf" ]]; then
    RUN_PY=false
elif [[ "${1:-}" == "--py" ]]; then
    RUN_NF=false
fi

PASS=0
FAIL=0

run() {
    local label="$1"; shift
    echo ""
    echo "=== ${label} ==="
    if "$@"; then
        echo "PASS: ${label}"
        ((PASS++))
    else
        echo "FAIL: ${label}"
        ((FAIL++))
    fi
}

# ---------------------------------------------------------------------------
# Nextflow stub tests
# ---------------------------------------------------------------------------
if [[ "${RUN_NF}" == true ]]; then
    module load nextflow 2>/dev/null || true
    run "Nextflow stub — FASTQ path"   bash "${DELIVER_DIR}/test_stub.sh"
    run "Nextflow stub — counts path"  bash "${DELIVER_DIR}/test_stub.sh" --counts
fi

# ---------------------------------------------------------------------------
# Python unit tests
# ---------------------------------------------------------------------------
if [[ "${RUN_PY}" == true ]]; then
    module load python/3.12.4 2>/dev/null || true
    source "${DELIVER_DIR}/.venv/bin/activate"
    run "Python unit tests" pytest "${DELIVER_DIR}/tests/" -v
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "================================"
echo "Results: ${PASS} passed, ${FAIL} failed"
echo "================================"

[[ "${FAIL}" -eq 0 ]]
