#!/bin/bash
# Run nf_job_summary.py without needing to manually load the Python module.
# Usage (from DELIVER root):
#   bash scripts/slurm_analysis/nf_job_summary.sh [--log .nextflow.log] [--seff]

module load python/3.12.4
python "$(dirname "$0")/nf_job_summary.py" "$@"
