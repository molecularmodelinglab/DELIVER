#!/bin/bash

#SBATCH --job-name=build_baylor_library_dict
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=1
#SBATCH --mem=1G
#SBATCH --time=0:05:00

# Usage (run from DELIVER root):
#   sbatch scripts/build_baylor_library_dict/build_baylor_library_dict.slurm \
#     --input-dir /proj/tropshalab/shared/Baylor/libraries/OpenDEL_v3 \
#     --output    /proj/tropshalab/shared/Baylor/utilities/libraries/OpenDEL_v3/library_dict.json

SCRIPT_DIR="${SLURM_SUBMIT_DIR}/scripts/build_baylor_library_dict"

module load python/3.12.4

python "${SCRIPT_DIR}/build_baylor_library_dict.py" "$@"
