#!/bin/bash

#SBATCH --job-name=rarefaction
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=12:00:00

# Usage:
#   sbatch submit_rarefaction.slurm \
#     --decoded-dir /path/to/original/deliver/work \
#     --nf-work-dir /path/to/rarefaction/work \
#     --cache-dir   /path/to/cache \
#     --out-dir     /path/to/output \
#     --log-dir     /path/to/logs \
#     [--fractions "0.05,0.10,0.25,0.50,0.75,0.90,1.00"] \
#     [--seed 42] \
#     [--deli-data-dir /path/to/deli_data] \
#     [--resume]
#
#   --decoded-dir   work directory of the original DELIVER run (*_decoded.tsv files)
#   --nf-work-dir   Nextflow work directory for this rarefaction run (intermediate files)
#   --cache-dir     Nextflow cache directory (use a per-user path)
#
# Tip: use per-user --nf-work-dir and --cache-dir to avoid conflicts.

DELIVER_DIR="${SLURM_SUBMIT_DIR}"
RESUME=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --decoded-dir)   DECODED_DIR="$2";   shift 2 ;;
        --nf-work-dir)   NF_WORK_DIR="$2";   shift 2 ;;
        --cache-dir)     CACHE_DIR="$2";     shift 2 ;;
        --out-dir)       OUT_DIR="$2";       shift 2 ;;
        --log-dir)       LOG_DIR="$2";       shift 2 ;;
        --fractions)     FRACTIONS="$2";     shift 2 ;;
        --seed)          SEED="$2";          shift 2 ;;
        --deli-data-dir) DELI_DATA_DIR="$2"; shift 2 ;;
        --resume)        RESUME=true;        shift   ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [ -z "$DECODED_DIR" ] || [ -z "$NF_WORK_DIR" ] || [ -z "$CACHE_DIR" ] || [ -z "$OUT_DIR" ] || [ -z "$LOG_DIR" ]; then
    echo "Usage: sbatch submit_rarefaction.slurm --decoded-dir DIR --nf-work-dir DIR --cache-dir DIR --out-dir DIR --log-dir DIR [options]"
    exit 1
fi

mkdir -p "$LOG_DIR" "$OUT_DIR" "$NF_WORK_DIR" "$CACHE_DIR"

exec > "$LOG_DIR/rarefaction_$(date +%Y%m%d_%H%M%S).log" 2>&1
export NXF_LOG_FILE="$LOG_DIR/nextflow_rarefaction_$(date +%Y%m%d_%H%M%S).log"
export NXF_CACHE_DIR="$CACHE_DIR"

module purge
module load python/3.12.4
module load nextflow

source "${DELIVER_DIR}/.venv/bin/activate"

RESUME_FLAG=""
$RESUME && RESUME_FLAG="-resume"

EXTRA_ARGS=""
[ -n "$FRACTIONS" ]     && EXTRA_ARGS="$EXTRA_ARGS --fractions '$FRACTIONS'"
[ -n "$SEED" ]          && EXTRA_ARGS="$EXTRA_ARGS --seed $SEED"
[ -n "$DELI_DATA_DIR" ] && EXTRA_ARGS="$EXTRA_ARGS --deli_data_dir $DELI_DATA_DIR"

nextflow run "${DELIVER_DIR}/scripts/rarefaction/rarefaction.nf" \
  $RESUME_FLAG \
  -profile longleaf \
  -c "${DELIVER_DIR}/scripts/rarefaction/rarefaction.config" \
  -w "$NF_WORK_DIR" \
  --decoded_dir "$DECODED_DIR" \
  --out_dir     "$OUT_DIR" \
  $EXTRA_ARGS
