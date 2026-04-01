# DELIVER

Nextflow pipeline for DEL (DNA Encoded Library) data processing on Longleaf HPC.

**We are using the "patch" branch of DELi as of now:** https://github.com/Popov-Lab-UNC/DELi/tree/patch

## Quick start

```bash
# One-time setup on login node
bash setup.sh
```

Edit `params.yml`, then submit:

```bash
sbatch submit.slurm \
  --deliver-dir /path/to/DELIVER \
  --work-dir    /path/to/work \
  --params-file /path/to/DELIVER/params.yml \
  --log-dir     /path/to/logs
```

## Visualize the workflow

```bash
cd /path/to/DELIVER
module load nextflow
nextflow run pipeline/main.nf \
  -with-dag dag.html \
  -params-file params.yml \
  -profile local \
  -preview
```

Opens as `dag.html` in the browser.

## Run modes

The pipeline detects the mode automatically from `params.yml`:

| `params.yml` | What runs |
|--------------|-----------|
| `read_1` set | FASTQ → preprocess → DELi → postprocessing |
| `counts_file` set | counts.parquet → postprocessing only |
| both set | error |
| neither set | error |

Add `--resume` to resume after failure:

```bash
sbatch submit.slurm \
  --deliver-dir /path/to/DELIVER \
  --work-dir    /path/to/work \
  --params-file /path/to/DELIVER/params.yml \
  --log-dir     /path/to/logs \
  --resume
```

## Testing

```bash
bash test.sh            # all tests
bash test.sh --nf       # Nextflow stub tests only (no DELi or fastp required)
bash test.sh --py       # Python unit tests only
```

Python unit tests for postprocessing scripts are in `tests/`. They will grow as `deduplicate.py` and `enrichment.py` are implemented.

## Repository structure

```
DELIVER/
├── params.yml                        # the only file users need to edit
├── setup.sh                          # one-time setup: creates .venv, installs DELi
├── submit.slurm                      # SLURM launcher
├── pipeline/
│   ├── main.nf                       # entry points: FULL, FROM_FASTQ, FROM_COUNTS
│   ├── nextflow.config               # longleaf / local profiles
│   └── subworkflows/
│       ├── preprocess.nf             # CONCAT + FASTP_MERGE (paired-end merge)
│       ├── deli.nf                   # DELi processes + DELI workflow
│       └── postprocess.nf            # DEDUPLICATE + ENRICHMENT workflows
├── src/
│   └── deliver/
│       └── postprocess/              # standalone Click CLI scripts called by NF
│           ├── deduplicate.py        # deduplication + aggregation (TODO)
│           └── enrichment.py         # enrichment scoring (TODO)
└── scripts/                          # one-shot developer utilities
```

## Pipeline stages

| Stage | Status |
|-------|--------|
| Preprocessing: concat lanes, merge paired-end reads (fastp) | implemented |
| DELi decoding: chunk → decode → collect → count → summarize → report | implemented |
| Deduplication + aggregation | stub (TODO) |
| Enrichment scoring | stub (TODO) |

## params.yml

The only file you need to edit. All parameters are documented inline in `params.yml`. Key sections:

### Input

| Parameter | Description |
|-----------|-------------|
| `read_1` | Read 1 sequencing file(s) — one or more lanes, `.fastq` or `.fastq.gz` |
| `read_2` | Read 2 sequencing file(s) — paired-end only; omit for single-end |
| `counts_file` | Pre-computed `counts.parquet` — set instead of `read_1` to skip decoding |
| `out_dir` | Directory where all results will be written |
| `deli_data_dir` | Path to DELi data directory (library definitions, building blocks) |

### Selection metadata

Written into the generated `decode.yaml` and used to name output files.

| Parameter | Description |
|-----------|-------------|
| `selection_id` | Short identifier for this selection (used as output file prefix) |
| `target_id` | Target protein name |
| `selection_condition` | Free-text description of selection conditions |
| `date_ran` | Date the selection was run (`YYYY-MM-DD`) |
| `libraries` | List of library IDs to decode against (must exist in `deli_data_dir`) |

### Decode settings

Defaults work for most cases. See [DELi docs](https://github.com/Popov-Lab-UNC/DELi) for details.

| Parameter | Default | Description |
|-----------|---------|-------------|
| `library_error_tolerance` | `2` | Max mismatches when matching a library barcode |
| `min_library_overlap` | `8` | Min bases overlapping between read and barcode |
| `revcomp` | `YES` | Reverse-complement reads before decoding |
| `demultiplexer_algorithm` | `regex` | Barcode finding algorithm (`regex` or `cutadapt`) |
| `demultiplexer_mode` | `single` | `single` — one library per read; `library` — split by library tag |
| `realign` | `NO` | Realign reads after initial barcode calling |
| `wiggle` | `YES` | Allow 1-base wiggle when locating barcode sections |
| `chunk_size` | `1000000` | Reads per FASTQ chunk (controls parallelism) |

## Tuning resources

Resource settings (CPUs, memory, time) can be tuned in the `longleaf` profile in `pipeline/nextflow.config`.

## Dependencies

- **Python 3.12.4** — `module load python/3.12.4`
- **Nextflow** — `module load nextflow`
- **fastp/1.0.1[1]** — `module load fastp/1.0.1` (loaded automatically by Nextflow on longleaf)
- **DELi[2]** — installed into `.venv` by `setup.sh`; decoding processes in `pipeline/subworkflows/deli.nf` are adapted from [DELi's Nextflow workflow](https://github.com/Popov-Lab-UNC/DELi)

[1] Shifu Chen. 2025. fastp 1.0: An ultra-fast all-round tool for FASTQ data quality control and preprocessing. iMeta 2025: https://doi.org/10.1002/imt2.107

[2]Wellnitz J, Novy B, Maxfield T, Lin S-H, Zhilinskaya I, Axtman M, Leisner T, Merten E, Norris-Drouin JL, Hardy BP, Pearce KH, Popov KI. (2025). *Open-Source DNA-Encoded Library informatics Package for Design, Decoding, and Analysis: DELi*. bioRxiv. https://doi.org/10.1101/2025.02.25.640184
