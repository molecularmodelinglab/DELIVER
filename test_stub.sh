#!/usr/bin/env bash
# Run the Nextflow pipeline in stub mode to verify workflow structure
# without executing any real tools (DELi, fastp, etc.)
#
# Usage:
#   bash test_stub.sh                  # test FASTQ path (default)
#   bash test_stub.sh --counts         # test counts path

set -euo pipefail

DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_DIR="${DELIVER_DIR}/.stub_work"
OUT_DIR="${DELIVER_DIR}/.stub_out"

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------
MODE="fastq"
if [[ "${1:-}" == "--counts" ]]; then
    MODE="counts"
fi

# ---------------------------------------------------------------------------
# Create minimal stub input files
# ---------------------------------------------------------------------------
STUB_DIR="${DELIVER_DIR}/.stub_inputs"
mkdir -p "${STUB_DIR}" "${OUT_DIR}"

STUB_FASTQ="${STUB_DIR}/stub_R1.fastq"
STUB_COUNTS="${STUB_DIR}/stub_counts.parquet"

# Minimal valid FASTQ (1 read) — enough for splitFastq to produce 1 chunk
printf "@stub_read1\nACGTACGT\n+\nIIIIIIII\n" > "${STUB_FASTQ}"
touch "${STUB_COUNTS}"

# ---------------------------------------------------------------------------
# Write a minimal params file for stub run
# ---------------------------------------------------------------------------
PARAMS_FILE="${STUB_DIR}/params_stub.yml"

if [[ "${MODE}" == "fastq" ]]; then
    cat > "${PARAMS_FILE}" <<EOF
forward_reads:
  - "${STUB_FASTQ}"
counts_file: null
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
deli_dir: "${STUB_DIR}"
selection_id:         "stub"
target_id:            "stub"
selection_condition:  "-"
date_ran:             "2024-01-01"
additional_info:      ""
libraries:
  - "L01"
library_error_tolerance:  2
min_library_overlap:      8
revcomp:                  "YES"
demultiplexer_algorithm:  "regex"
demultiplexer_mode:       "single"
realign:                  "NO"
wiggle:                   "YES"
chunk_size: 1000000
prefix:     ""
debug:      false
fastp_threads: 4
EOF
else
    cat > "${PARAMS_FILE}" <<EOF
forward_reads: null
counts_file: "${STUB_COUNTS}"
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
deli_dir: "${STUB_DIR}"
selection_id:         "stub"
target_id:            "stub"
selection_condition:  "-"
date_ran:             "2024-01-01"
additional_info:      ""
libraries:
  - "L01"
library_error_tolerance:  2
min_library_overlap:      8
revcomp:                  "YES"
demultiplexer_algorithm:  "regex"
demultiplexer_mode:       "single"
realign:                  "NO"
wiggle:                   "YES"
chunk_size: 1000000
prefix:     ""
debug:      false
fastp_threads: 4
EOF
fi

# ---------------------------------------------------------------------------
# Load Nextflow and run
# ---------------------------------------------------------------------------
module load nextflow

echo "Running stub test (mode: ${MODE})..."
nextflow run "${DELIVER_DIR}/pipeline/main.nf" \
    -params-file "${PARAMS_FILE}" \
    -profile local \
    -work-dir "${WORK_DIR}" \
    -stub-run

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
rm -rf "${STUB_DIR}" "${WORK_DIR}" "${OUT_DIR}"

echo "Stub test passed."
