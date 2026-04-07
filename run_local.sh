#!/bin/bash
# Local Mac run script — alternative to submit.slurm for running outside Longleaf HPC.
#
# Prerequisites:
#   1. Run setup_local.sh once to create .venv with DELi installed
#   2. Nextflow must be installed: https://www.nextflow.io/docs/latest/install.html
#
# Usage:
#   bash run_local.sh           # fresh run
#   bash run_local.sh --resume  # resume after failure

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMS_FILE="${SCRIPT_DIR}/params_local.yml"
WORK_DIR="${SCRIPT_DIR}/work"
LOG_DIR="${SCRIPT_DIR}/logs"

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
RESUME_FLAG=""
for arg in "$@"; do
    if [ "$arg" = "--resume" ]; then
        RESUME_FLAG="-resume"
    fi
done

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------
if [ ! -d "${SCRIPT_DIR}/.venv" ]; then
    echo "Error: .venv not found. Run setup_local.sh first."
    exit 1
fi

if ! command -v nextflow &>/dev/null; then
    echo "Error: nextflow not found. Install from https://www.nextflow.io/docs/latest/install.html"
    exit 1
fi

if [ ! -f "${PARAMS_FILE}" ]; then
    echo "Error: params_local.yml not found in ${SCRIPT_DIR}"
    exit 1
fi

# ---------------------------------------------------------------------------
# Activate venv so DELi CLI and Python are available to Nextflow processes
# ---------------------------------------------------------------------------
source "${SCRIPT_DIR}/.venv/bin/activate"

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
echo "Starting DELIVER pipeline (local profile)..."
echo "  params:   ${PARAMS_FILE}"
echo "  work dir: ${WORK_DIR}"
echo "  log:      ${SCRIPT_DIR}/.nextflow.log"
echo ""

cd "${SCRIPT_DIR}"
nextflow run pipeline/main.nf \
    -profile local \
    -params-file "${PARAMS_FILE}" \
    -work-dir "${WORK_DIR}" \
    ${RESUME_FLAG}
