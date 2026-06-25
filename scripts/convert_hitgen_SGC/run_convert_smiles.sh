#!/usr/bin/env bash
# Submit one SLURM job per SGC-DEL .txt.gz file.
#
# Usage (run from DELIVER root):
#   bash scripts/convert_hitgen_SGC/run_convert_smiles.sh \
#     --input-dir  /path/to/sgc/enumerated/raw_data \
#     --output-dir /path/to/output/enumerated_smiles

set -euo pipefail

DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

INPUT_DIR=""
OUTPUT_DIR=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --input-dir)  INPUT_DIR="$2";  shift 2 ;;
        --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [[ -z "${INPUT_DIR}" || -z "${OUTPUT_DIR}" ]]; then
    echo "Usage: bash run_convert_smiles.sh --input-dir DIR --output-dir DIR"
    exit 1
fi

mkdir -p "${OUTPUT_DIR}"

for INPUT in "${INPUT_DIR}"/SGC-DEL*.txt.gz; do
    LIB=$(basename "${INPUT}" _Fully_Enumerated_Structures.txt.gz)
    sbatch --job-name="smiles_${LIB}" \
           --export=ALL,INPUT="${INPUT}",OUTPUT_DIR="${OUTPUT_DIR}" \
           --chdir="${DELIVER_DIR}" \
           "${DELIVER_DIR}/scripts/convert_hitgen_SGC/convert_smiles.slurm"
    echo "Submitted: ${LIB}"
done
