#!/bin/bash

#SBATCH --job-name=convert_hitgen
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --time=1:00:00

# Usage (run from DELIVER root):
#   sbatch scripts/convert_hitgen/convert_hitgen.slurm \
#     --input-dir  /path/to/hitgen/tsv_files \
#     --output-dir /path/to/deli_data \
#     --config     scripts/convert_hitgen/library_config.yml

SCRIPT_DIR="${SLURM_SUBMIT_DIR}/scripts/convert_hitgen"
VENV_DIR="${SCRIPT_DIR}/.venv"

module load python/3.12.4

# Create venv on first run
if [ ! -d "${VENV_DIR}" ]; then
    echo "Creating venv at ${VENV_DIR}..."
    python -m venv "${VENV_DIR}"
    source "${VENV_DIR}/bin/activate"
    pip install -q -r "${SCRIPT_DIR}/requirements.txt"
else
    source "${VENV_DIR}/bin/activate"
fi

python "${SCRIPT_DIR}/convert_hitgen.py" "$@"
