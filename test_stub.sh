#!/usr/bin/env bash
# Run the Nextflow pipeline in stub mode to verify workflow structure
# without executing any real tools (DELi, fastp, etc.)
#
# Usage:
#   bash test_stub.sh                  # test FASTQ path (default)
#   bash test_stub.sh --counts         # test counts path
#   bash test_stub.sh --merged         # test pre-merged FASTQ path

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
elif [[ "${1:-}" == "--merged" ]]; then
    MODE="merged"
elif [[ "${1:-}" == "--lanes" ]]; then
    # paired-end, 2 lanes (one .ora, one plain), 3 libraries whose names
    # stress per-library routing (L1 vs L11, underscores)
    MODE="lanes"
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

if [[ "${MODE}" == "lanes" ]]; then
    for f in SMP_S1_L001_R1_001.fastq.ora SMP_S1_L001_R2_001.fastq.ora SMP_S1_L002_R1_001.fastq SMP_S1_L002_R2_001.fastq; do
        cp "${STUB_FASTQ}" "${STUB_DIR}/${f}"
    done
    cat > "${PARAMS_FILE}" <<EOF
read_1:
  - "${STUB_DIR}/SMP_S1_L001_R1_001.fastq.ora"
  - "${STUB_DIR}/SMP_S1_L002_R1_001.fastq"
read_2:
  - "${STUB_DIR}/SMP_S1_L001_R2_001.fastq.ora"
  - "${STUB_DIR}/SMP_S1_L002_R2_001.fastq"
counts: null
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
selection_id:         "stub"
target_id:            "stub"
selection_condition:  "-"
date_ran:             "2024-01-01"
additional_info:      ""
libraries:
  - "L1"
  - "L11"
  - "SGC_DEL_01"
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
elif [[ "${MODE}" == "fastq" ]]; then
    cat > "${PARAMS_FILE}" <<EOF
read_1:
  - "${STUB_FASTQ}"
counts: null
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
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
elif [[ "${MODE}" == "merged" ]]; then
    cat > "${PARAMS_FILE}" <<EOF
read_1: null
counts: null
merged_fastq: "${STUB_FASTQ}"
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
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
read_1: null
counts:
  file:   "${STUB_COUNTS}"
  format: "deli"
out_dir: "${OUT_DIR}"
deli_data_dir: "${STUB_DIR}"
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
# `module` only exists on the HPC; locally nextflow is expected on PATH.
if ! command -v nextflow &>/dev/null; then
    module load nextflow
fi

echo "Running stub test (mode: ${MODE})..."
nextflow run "${DELIVER_DIR}/pipeline/main.nf" \
    -params-file "${PARAMS_FILE}" \
    -profile local \
    -work-dir "${WORK_DIR}" \
    -with-trace "${STUB_DIR}/trace.txt" \
    -stub-run

# Lanes mode: every lane runs its own chain, and collect runs once per library.
if [[ "${MODE}" == "lanes" ]]; then
    expect() {  # expect <process> <count>
        local n
        n=$(awk -F'\t' -v p="$1" '$4 ~ ":"p"$" && $6 == "COMPLETED"' "${STUB_DIR}/trace.txt" | wc -l | tr -d ' ')
        if [[ "$n" != "$2" ]]; then
            echo "FAIL: expected $2 x $1, got $n" >&2
            exit 1
        fi
        echo "  ok: $2 x $1"
    }
    expect ORA_DECOMPRESS 2        # only lane L001 is .ora (R1 + R2)
    expect FASTQC 2
    expect FASTP_MERGE 2
    expect SPLIT 2
    expect CollectDecodeChunks 3   # L1, L11, SGC_DEL_01, none misrouted
    for f in L001_fastp.json L002_fastp.json; do
        [[ -f "${OUT_DIR}/qc/${f}" ]] || { echo "FAIL: missing qc/${f}" >&2; exit 1; }
    done
    echo "  ok: per-lane fastp reports published"
fi

# ---------------------------------------------------------------------------
# Cleanup (|| true: transient "Directory not empty" errors on Lustre are benign)
# ---------------------------------------------------------------------------
rm -rf "${STUB_DIR}" "${WORK_DIR}" "${OUT_DIR}" || true

echo "Stub test passed."
