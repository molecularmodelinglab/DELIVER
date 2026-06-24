# Rarefaction analysis

Runs a saturation curve analysis on an already-decoded DELIVER run.
Subsamples the decoded reads at different fractions (e.g. 5%, 10%, 25%, …, 100%),
then re-runs DELi counting and UMI deduplication on each fraction in parallel.
The output lets you see whether the sequencing depth was sufficient to observe
most of the library — if compound counts are still rising steeply at 90%, you
need more reads.

## What it reads

- `*_decoded.tsv` files from a completed Nextflow work directory (produced by
  `DELI:DecodeChunk`). Each file is one FASTQ chunk decoded by DELi, with
  columns `library_id`, `bb_ids`, `umi`, `library_score`, `bb_scores`,
  `overall_score`.

Sampling is done at the **chunk-file level**: a random subset of the
`*_decoded.tsv` files is selected for each fraction. With ~400 chunks this is
accurate enough for a saturation curve and avoids loading hundreds of millions
of rows into memory.

## What it does

For each fraction:

1. **`COLLECT_SUBSAMPLE`** — randomly selects `round(N_chunks × fraction)` decoded
   TSV files and runs `deli decode collect` on them, producing a single
   `.ndjson` file where each line is a unique compound with its raw UMI list.
2. **`COUNT_FRACTION`** — runs `deli decode count --cluster-umis` on the ndjson,
   applying UMI clustering/deduplication and producing a `counts.parquet` with
   `raw_count` and `dedup_count` per compound.

All fractions run as separate parallel SLURM jobs, so wall time ≈ time for the
largest fraction (100%).

After all fractions complete:

3. **`RAREFACTION_SUMMARY`** — reads all per-fraction `counts.parquet` files and
   writes a `rarefaction_summary.csv` with one row per fraction.

## Output

```
<out-dir>/
├── 005pct/
│   ├── 005pct_collected.ndjson
│   └── 005pct_counts.parquet
├── 010pct/
│   └── ...
├── 025pct/  050pct/  075pct/  090pct/  100pct/
└── rarefaction_summary.csv        ← main result
```

`rarefaction_summary.csv` columns:

| Column | Description |
|--------|-------------|
| `fraction` | Fraction of chunks used (0.05 – 1.00) |
| `label` | Human-readable label (e.g. `050pct`) |
| `n_compounds` | Number of unique compounds after UMI deduplication |
| `total_reads` | Sum of raw read counts across all compounds |
| `dedup_reads` | Sum of deduplicated (UMI-corrected) counts |

## Usage

Run from the **DELIVER root directory**:

```bash
sbatch scripts/rarefaction/submit_rarefaction.slurm \
  --decoded-dir /work/users/$USER/work_sk_s1_r1 \
  --nf-work-dir /work/users/$USER/work_rarefaction \
  --cache-dir   /users/$USER/cache_rarefaction \
  --out-dir     /users/$USER/rarefaction_out \
  --log-dir     /users/$USER/rarefaction_logs
```

`--decoded-dir` is the work directory of the **original completed DELIVER run**
(contains the `*_decoded.tsv` files). `--nf-work-dir` is a fresh directory for
this rarefaction run's own intermediate files — keep them separate.

### All options

| Flag | Required | Default | Description |
|------|----------|---------|-------------|
| `--decoded-dir` | yes | — | Work directory of the original DELIVER run (contains `*_decoded.tsv`) |
| `--nf-work-dir` | yes | — | Work directory for this rarefaction run's intermediate files |
| `--cache-dir` | yes | — | Nextflow cache directory (use a per-user path) |
| `--out-dir` | yes | — | Directory to write results into |
| `--log-dir` | yes | — | Directory for launcher and Nextflow logs |
| `--fractions` | no | `"0.05,0.10,0.25,0.50,0.75,0.90,1.00"` | Comma-separated list of fractions to test |
| `--seed` | no | `42` | Random seed for reproducibility |
| `--deli-data-dir` | no | — | DELi data directory (passed to `deli` if needed) |
| `--resume` | no | — | Resume a previously interrupted run |

> **Multiple users:** use separate `--work-dir` and `--cache-dir` per user to
> avoid conflicts.
