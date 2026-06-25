#!/bin/bash

#SBATCH --job-name=smiles_SGC
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=128G
#SBATCH --time=4:00:00
#SBATCH --output=slurm-%j-smiles_%x.out

# Usage (via run_convert_smiles.sh, or manually):
#   sbatch --export=ALL,INPUT=/path/to/SGC-DEL0001_Fully_Enumerated_Structures.txt.gz,OUTPUT_DIR=/path/to/out \
#          scripts/convert_hitgen_SGC/convert_smiles.slurm

SCRIPT_DIR="${SLURM_SUBMIT_DIR}/scripts/convert_hitgen_SGC"
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

python "${SCRIPT_DIR}/convert_smiles.py" \
    --input        "${INPUT}" \
    --output-dir   "${OUTPUT_DIR}" \
    --temp-dir     "${TMPDIR}" \
    --memory-limit "112GB"
