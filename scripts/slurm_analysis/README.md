# slurm_analysis

Parses a Nextflow `.nextflow.log` file and reports per-job SLURM efficiency stats.

## Run (from DELIVER root)

```bash
# Basic — durations from the log only
bash scripts/slurm_analysis/nf_job_summary.sh

# With seff — wall-clock time, CPU and memory efficiency per job
bash scripts/slurm_analysis/nf_job_summary.sh --seff

# With seff + config — also shows requested cpus/memory/time from nextflow.config
bash scripts/slurm_analysis/nf_job_summary.sh \
  --seff \
  --config pipeline/nextflow.config

# Save to CSV (creates jobs.csv and jobs_summary.csv)
bash scripts/slurm_analysis/nf_job_summary.sh \
  --seff \
  --config pipeline/nextflow.config \
  --output jobs.csv
```

## Options

| Flag | Default | Description |
|------|---------|-------------|
| `--log FILE` | `.nextflow.log` | Nextflow log to parse |
| `--seff` | off | Query `seff` per job for wall-clock time and CPU/memory efficiency |
| `--config FILE` | — | Nextflow config to read requested resources from (longleaf profile) |
| `--output FILE` | — | Save per-job table to CSV; summary saved as `<name>_summary.csv` |

## Output

Two tables are printed:

**Per-job table** — one row per SLURM job:

```
job_id    process                status     exit  started              exited               dur(min)  wall_clock  cpu_eff  mem_used  mem_eff
42039175  PREPROCESS:CONCAT (1)  COMPLETED  0     2026-04-07 15:43:37  2026-04-07 15:45:40  2.05      00:02:03    85.71%   4.23 GB   84.60%
```

**Summary by process type** — aggregated with mean ± SEM and requested resources:

```
process    n    avg_wall_min  sem_wall_min  avg_cpu_eff_%  ...  req_cpus  req_memory  req_time
DecodeChunk  655  6.23        0.04          96.70          ...  1         4 GB        15m
```

> **Note on `dur(min)`:** this value is computed from Nextflow's internal polling timestamps and is unreliable for short jobs (< 1 min). Use `wall_clock` from `--seff` for accurate timing.

> **Note on `--seff` availability:** SLURM retains job accounting data for a limited time (typically 30–90 days). Old jobs may return empty seff output.
