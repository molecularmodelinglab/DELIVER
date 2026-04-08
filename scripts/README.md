# scripts/

One-shot developer utilities (vendor format conversions, data prep, etc.).

These scripts are **not part of the Nextflow pipeline**.

## Available scripts

| Directory | What it does |
|-----------|--------------|
| [`convert_hitgen/`](convert_hitgen/README.md) | Convert Hitgen library TSV files to DELi format |
| [`slurm_analysis/`](slurm_analysis/README.md) | Parse Nextflow log and report SLURM job efficiency |

## Conventions

- Each script lives in its own subdirectory with a `README.md` showing an example run.
- `convert_hitgen` requires its own `.venv` (created automatically on first SLURM run).
- `slurm_analysis` uses the system Python module (`module load python/3.12.4`) — no extra dependencies.
