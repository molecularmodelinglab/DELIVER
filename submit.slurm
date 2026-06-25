#!/bin/bash

#SBATCH --job-name=deliver
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --time=06:00:00

# Usage:
#   sbatch submit.slurm --work-dir DIR --cache-dir DIR --params-file FILE --log-dir DIR [--resume]
#
#   --work-dir      Nextflow work directory (intermediate files)
#   --cache-dir     Nextflow cache directory (.nextflow metadata; use a per-user path)
#   --params-file   path to params.yml
#   --log-dir       directory for launcher logs
#   --resume        resume after failure
#
# The pipeline detects the run mode from params.yml automatically:
#   read_1 set      → FASTQ input (preprocess → DELi → postprocess)
#   counts_file set → counts.parquet input (postprocess only)
#
# Example:
#   sbatch submit.slurm \
#     --work-dir    /path/to/work \
#     --cache-dir   /path/to/cache \
#     --params-file /path/to/DELIVER/params.yml \
#     --log-dir     /path/to/logs \
#     --resume

DELIVER_DIR="${SLURM_SUBMIT_DIR}"
RESUME=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)     WORK_DIR="$2";    shift 2 ;;
        --cache-dir)    CACHE_DIR="$2";   shift 2 ;;
        --params-file)  PARAMS_FILE="$2"; shift 2 ;;
        --log-dir)      LOG_DIR="$2";     shift 2 ;;
        --resume)       RESUME=true;      shift   ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

if [ -z "$WORK_DIR" ] || [ -z "$CACHE_DIR" ] || [ -z "$PARAMS_FILE" ] || [ -z "$LOG_DIR" ]; then
    echo "Usage: sbatch submit.slurm --work-dir DIR --cache-dir DIR --params-file FILE --log-dir DIR [--resume]"
    exit 1
fi

mkdir -p "$LOG_DIR"
mkdir -p "$WORK_DIR"
mkdir -p "$CACHE_DIR"

exec > "$LOG_DIR/launcher_$(date +%Y%m%d_%H%M%S).log" 2>&1

cd "$DELIVER_DIR" || exit 1
module purge
module load python/3.12.4
module load nextflow

source "${DELIVER_DIR}/.venv/bin/activate"

export DELI_DATA_DIR=$(python3 -c "import yaml; print(yaml.safe_load(open('${PARAMS_FILE}')).get('deli_data_dir', '') or '')")
export NXF_CACHE_DIR="$CACHE_DIR"
export NXF_LOG_FILE="$LOG_DIR/nextflow_$(date +%Y%m%d_%H%M%S).log"

RESUME_FLAG=""
$RESUME && RESUME_FLAG="-resume"

nextflow run pipeline/main.nf \
  $RESUME_FLAG \
  -profile longleaf \
  -w "$WORK_DIR" \
  -params-file "$PARAMS_FILE"
