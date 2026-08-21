# DELIVER

Nextflow pipeline for DEL (DNA Encoded Library) data processing.

**We are using the "patch" branch of DELi as of now:** https://github.com/Popov-Lab-UNC/DELi/tree/patch

For a detailed technical walkthrough of every pipeline step, see [docs/details.md](docs/details.md). For common Longleaf failure modes and how to recover, see [docs/troubleshoot.md](docs/troubleshoot.md).

## Quick start — Longleaf HPC

```bash
# One-time setup on the login node — loads Python 3.12.4 and creates .venv
bash setup.sh --deli-dir /path/to/DELi
```

Edit the remaining fields in `params.yml` (see [parameter reference](#paramsyml) below), then submit. Each pipeline step runs as a separate SLURM job — see [How the pipeline runs on Longleaf](#how-the-pipeline-runs-on-longleaf) for details.

**Run sbatch command from the DELIVER directory**.

```bash
cd /path/to/DELIVER
sbatch submit.slurm \
  --work-dir    /path/to/work \
  --cache-dir   /path/to/cache \
  --params-file /path/to/DELIVER/params.yml \
  --log-dir     /path/to/logs
```

> **Multiple users sharing this repo:** use separate `--work-dir` and `--cache-dir` paths per user (e.g. `/proj/tropshalab/shared/deliver/work/$USER` and `/proj/tropshalab/shared/deliver/cache/$USER`) so runs do not interfere with each other and `--resume` works correctly.

## Quick start — GCP Cloud Batch

Runs the pipeline on Google Cloud Batch using the `gcp` profile in `pipeline/nextflow.config`.

Requires: `nextflow`, `gcloud` CLI (authenticated via `gcloud auth application-default login`), `docker`, `java`, `python3` with `pyyaml`, and a GCS bucket + GCP project you have access to.

### 1. Create `.env`

Both `submit_gcp.sh` and `build_and_push.sh` read all GCP configuration from a `.env` file at the repo root. It is gitignored — your project IDs, buckets, and service account stay local.

Create `DELIVER/.env` with these variables (no spaces around `=`, use quotes for values with special characters):

```bash
# GCP project & region
PROJECT="my-gcp-project"
REGION="us-central1"

# Storage
BUCKET="my-gcs-bucket"
WORK_DIR="gs://my-gcs-bucket/deliver-work/"
LOG_DIR="gs://my-gcs-bucket/deliver-logs"

# Pipeline run config (relative paths are resolved from repo root)
PARAMS_FILE="params.yml"

# Container image (Artifact Registry)
REPO_NAME="deliver-repo"
IMAGE_NAME="deliver"
TAG="latest"
CONTAINER_REGISTRY="us-central1-docker.pkg.dev/my-gcp-project/deliver-repo/deliver:latest"

# Cloud Batch service account
SERVICE_ACCOUNT="my-sa@my-gcp-project.iam.gserviceaccount.com"
```

| Variable | Used by | What to set |
|----------|---------|-------------|
| `PROJECT` | both | GCP project ID |
| `REGION` | both | GCP region (e.g. `us-central1`) |
| `BUCKET` | submit | GCS bucket name (no `gs://` prefix) |
| `WORK_DIR` | submit | GCS path for Nextflow work directory |
| `LOG_DIR` | submit | Local or GCS path for launcher logs |
| `PARAMS_FILE` | submit | Path to your `params.yml` |
| `REPO_NAME` | build | Artifact Registry repository name |
| `IMAGE_NAME` | build | Docker image name |
| `TAG` | build | Docker image tag |
| `CONTAINER_REGISTRY` | submit | Full image URI (must match `REGION`/`PROJECT`/`REPO_NAME`/`IMAGE_NAME`/`TAG`) |
| `SERVICE_ACCOUNT` | submit | Service account email used by Cloud Batch jobs |

If `.env` is missing, both scripts fail immediately with a clear message — there are no hardcoded fallbacks.

### 2. Build & push the Docker image

Cloud Batch jobs pull the pipeline image from Artifact Registry. `build_and_push.sh` enables the Artifact Registry API, creates the repository (idempotent), configures Docker auth, builds the image from the repo's `Dockerfile`, and pushes it.

Run this once before your first submission, and any time pipeline code or dependencies change:

```bash
chmod +x build_and_push.sh
./build_and_push.sh                        # uses values from .env
./build_and_push.sh --tag 1.0.0            # override TAG for this run
```

CLI flags `--project`, `--region`, and `--tag` override the corresponding `.env` values. The script prints the full image URI on success.

### 3. (Optional) Sanity-check GCP setup

Before committing to a full pipeline run, run `pipeline/gcp_sanity_check.nf` to verify that the container image, GCS access, and required tools (Python deps, `deli`, `fastp`, postprocess scripts, system tools) all work on a real Cloud Batch VM. Each check runs as its own parallel Cloud Batch job and the run exits non-zero on the first failure with a clear message.

```bash
nextflow run pipeline/gcp_sanity_check.nf \
    -c pipeline/nextflow.config \
    -profile gcp \
    -w gs://YOUR_BUCKET/deliver-work \
    --project YOUR_PROJECT \
    --bucket  YOUR_BUCKET \
    --region  us-central1
```

| Flag | Value |
|------|-------|
| `-w` | GCS path Nextflow uses as its work directory (matches `WORK_DIR` in `.env`) |
| `--project` | GCP project ID (matches `PROJECT` in `.env`) |
| `--bucket` | GCS bucket name, no `gs://` prefix (matches `BUCKET` in `.env`) |
| `--region` | GCP region, e.g. `us-central1` (matches `REGION` in `.env`) |

A successful run ends with `ALL CHECKS PASSED — ready for pipeline run`. Once this passes, proceed to step 4.

### 4. Submit

```bash
bash submit_gcp.sh                # uses values from .env
bash submit_gcp.sh --resume       # resume after failure
```

CLI flags `--work-dir`, `--params-file`, `--log-dir`, `--project`, `--bucket`, `--region` override the corresponding `.env` values, e.g.:

```bash
bash submit_gcp.sh \
  --project     my-other-project \
  --bucket      my-other-bucket \
  --params-file /path/to/other_params.yml
```

On a successful run, the work directory in GCS is automatically deleted; on failure it is preserved for debugging.

## Quick start — local Mac

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and [Nextflow](https://www.nextflow.io/docs/latest/install.html).
Requires DELi (patch branch)
Requires fastp.

```bash
# One-time setup: creates .venv with Python 3.13 and installs DELi
bash setup_local.sh
```

Create `params_local.yml` (gitignored) with your local paths — use `params.yml` as a template. Then:

```bash
bash run_local.sh           # fresh run
bash run_local.sh --resume  # resume after failure
```

Results go to the `out_dir` set in `params_local.yml`.

## Visualize the workflow

```bash
cd /path/to/DELIVER
module load nextflow
nextflow run pipeline/main.nf \
  -with-dag docs/dag.html \
  -params-file params.yml \
  -profile local \
  -preview
```

Opens as `docs/dag.html` in the browser. A pre-generated copy is kept at [docs/dag.html](docs/dag.html) and [docs/dag.png](docs/dag.png).

## Run modes

The pipeline detects the mode automatically from `params.yml`:

| `params.yml` | What runs |
|--------------|-----------|
| `read_1` set | FASTQ → preprocess → DELi → postprocessing |
| `counts` set | counts.parquet → postprocessing only |
| both set | error |
| neither set | error |

Add `--resume` to resume after failure:

```bash
sbatch submit.slurm \
  --work-dir    /path/to/work \
  --cache-dir   /path/to/cache \
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

Python unit tests for postprocessing scripts are in `tests/`.

## Repository structure

```
DELIVER/
├── params.yml                        # template — copy to params_local.yml for local runs
├── setup.sh                          # one-time setup for Longleaf: bash setup.sh --deli-dir /path/to/DELi
├── setup_local.sh                    # one-time setup for local Mac (uses uv + Python 3.13)
├── submit.slurm                      # SLURM launcher for Longleaf
├── run_local.sh                      # run script for local Mac
├── pipeline/
│   ├── main.nf                       # auto-detects mode from params
│   ├── nextflow.config               # longleaf / local profiles
│   └── subworkflows/
│       ├── preprocess.nf             # CONCAT + FASTP_MERGE (paired-end merge)
│       ├── deli.nf                   # DELi processes + DELI workflow
│       └── postprocess.nf            # BUILD_LIBRARY_DICT + NORMALIZE + ADD_SMILES_LIB + MERGE_SMILES + DEDUPLICATE + SINGLETON + JOIN + LABEL
├── src/
│   └── deliver/
│       └── postprocess/              # standalone Python CLI scripts called by NF
│           ├── columns.py            # column name constants
│           ├── common.py             # shared utilities (validate, load inputs)
│           ├── metrics.py            # metrics (binomial z-score, polyO)
│           ├── build_library_dict.py # build library dictionary JSON from DELi data
│           ├── normalize.py          # normalize DELi counts → common format
│           ├── normalize_custom.py   # normalize external counts with user-specified column mapping
│           ├── add_smiles.py         # join SMILES to normalized compounds (one job per library)
│           ├── merge_smiles.py       # merge per-library SMILES parquets into one
│           ├── deduplicate.py        # deduplication + aggregation
│           ├── singleton.py          # per-compound enrichment scores (z_score_lib_normalized, z_score_global_normalized, polyo)
│           ├── disynthons.py         # disynthon counts + statistics (AB, BC, AC, …) with z-scores and polyo
│           ├── join.py               # join singletons + disynthons into enriched.parquet (wide table)
│           └── label.py              # add label_* boolean columns per labeling mode
└── scripts/
    ├── convert_hitgen/               # Hitgen TSV → DELi format converter
    └── convert_hitgen_SGC/           # SGC-DEL Excel → DELi format + SMILES parquet converter
```

## Vendor data preparation

Before running the pipeline you need DELi-format library definitions. Use the conversion script matching your vendor:

**Hitgen:**

```bash
sbatch scripts/convert_hitgen/convert_hitgen.slurm \
  --input-dir  /path/to/hitgen/tsv_files \
  --output-dir /path/to/deli_data \
  --config     scripts/convert_hitgen/library_config.yml
```

See [scripts/convert_hitgen/README.md](scripts/convert_hitgen/README.md) for setup and input format details.

**SGC-DEL:**

```bash
sbatch scripts/convert_hitgen_SGC/convert_decoding.slurm \
  --input-dir  /path/to/sgc/excel_files \
  --output-dir /path/to/deli_data
```

For SMILES lookup, also convert the enumerated structures — see [scripts/convert_hitgen_SGC/README.md](scripts/convert_hitgen_SGC/README.md).

Both scripts create `libraries/` and `building_blocks/` inside `--output-dir`, which you then point `deli_data_dir` at in `params.yml`.

## Pipeline stages

| Stage | Status |
|-------|--------|
| Preprocessing: concat lanes, merge paired-end reads (fastp) | implemented |
| DELi decoding: chunk → decode → collect → count → summarize → report | implemented |
| Build library dictionary (library_dict.json) | implemented |
| Normalize DELi counts → common format + validation | implemented |
| Normalize external counts with user-specified column mapping | implemented |
| SMILES lookup: join per-library SMILES parquets (parallel, one job per library) | implemented |
| Deduplication + aggregation | implemented |
| Per-compound enrichment scores (z_score_lib_normalized, z_score_global_normalized[3], polyo[4]) — singletons.parquet | implemented |
| Disynthon counts + statistics (AB, BC, AC, …) with z-scores and polyo | implemented |
| Join singletons + disynthons into enriched.parquet (wide table) | implemented |
| Label compounds with boolean label_* columns (count, z-score, polyo modes) — labeled.parquet | implemented |

## params.yml

The only file you need to edit. All parameters are documented inline in `params.yml`. Key sections:

### Input

| Parameter | Description |
|-----------|-------------|
| `read_1` | Read 1 sequencing file(s) — one or more lanes, `.fastq` or `.fastq.gz` |
| `read_2` | Read 2 sequencing file(s) — paired-end only; omit for single-end |
| `counts` | Pre-computed counts parquet — set instead of `read_1` to skip decoding (see below) |
| `out_dir` | Directory where all results will be written |
| `deli_data_dir` | Path to DELi data directory (library definitions, building blocks). Not required when `library_dict` is set. |
| `library_dict` | Path to a pre-computed `library_dict.json`. When set, `BUILD_LIBRARY_DICT` is skipped and `deli_data_dir` is not needed. Useful in counts mode on a machine without DELi data. |
| `on_duplicate_compound_id` | What to do when the same `compound_id` appears more than once: `"fail"` (default) — abort with an error; `"sum"` — merge by summing counts. |

### counts (optional)

Set `counts` instead of `read_1` to skip decoding and run postprocessing only. `counts.format` is always required.

**DELi output format** — use when the file came from a previous DELIVER or DELi run:

```yaml
counts:
  file:   "/path/to/prefix_counts.parquet"
  format: "deli"
```

**External format** — use for files from other sources (Hitgen, SGC, your own processing). Specify how to find the compound identity and counts:

```yaml
counts:
  file:   "/path/to/counts.parquet"
  format: "external"
  corrected_count_col: "UMI_count"   # required: UMI-corrected count column

  # Compound identity — specify exactly ONE of:
  compound_col: "compound_id"        # (a) already in "library-bb1-bb2-bb3" format
  # OR
  library_col:  "library_id"         # (b) library ID column
  bb_ids_col:   "bb_ids"             #     + comma-separated bb IDs column
  # OR
  library_col:  "library_id"         # (c) library ID column
  cycle_cols:   [A, B, C]            #     + individual bb ID columns in cycle order

  # Optional:
  raw_count_col: "raw_count"         # raw read count — required for PolyO scores; omit to skip PolyO
  z_score_col:   "z_score"           # pre-calculated z-score — carried through, not recalculated
  smiles_col:    "SMILES"            # SMILES column — carried through to output
```

### Selection metadata

Written into the generated `decode.yaml` and used to name output files.

| Parameter | Description |
|-----------|-------------|
| `selection_id` | Short identifier for this selection (used as output file prefix) |
| `target_id` | Target protein name |
| `selection_condition` | Free-text description of selection conditions |
| `date_ran` | Date the selection was run (`YYYY-MM-DD`) |
| `libraries` | List of library IDs to decode against (must exist in `deli_data_dir`) |

### SMILES lookup (optional)

Omit the `smiles` block entirely to skip SMILES joining. When present, the pipeline runs one SLURM job per library in parallel to join SMILES, then merges results before deduplication.

```yaml
smiles:
  compound_col: compound   # column name for compound ID in the SMILES parquet files
  smiles_col:   SMILES     # column name for SMILES in the SMILES parquet files
  max_missing_fraction: 0.01   # optional, default 0.01 — see below
  files:
    L01: /path/to/L01_enumerated.parquet
    L02: /path/to/L02_enumerated.parquet
    # one entry per library
```

**SMILES parquet format** — each file must contain exactly two columns:

| Column | Type | Description |
|--------|------|-------------|
| `compound` (or value of `compound_col`) | `String` | Compound ID matching the `compound_id` column in `normalized.parquet`, e.g. `L01-1-1-1` |
| `SMILES` (or value of `smiles_col`) | `String` | SMILES string for the compound |

Lookup is a DuckDB join against each file, so no particular row order is required. Libraries not listed in `smiles.files` pass through with a `null` SMILES value.

**Missing/corrupted SMILES** — decode noise occasionally produces a compound ID with no match in the library's SMILES file, or a corrupted SMILES value. Per library, if the fraction of such compounds is at or below `max_missing_fraction` (default 1%), they're logged as a warning and kept with a `null` SMILES rather than failing the run. Above that fraction, the pipeline fails — it's more likely a real reference/decode mismatch than noise at that point. Either way, per-library coverage (`n_compounds`, `n_missing`, `n_corrupted`, `missing_fraction`) is written to `smiles_report.tsv` alongside `normalized.parquet`, worst coverage first.

### Labeling (optional)

Adds one `label_<mode>` boolean column per mode to `enriched.parquet` and writes `labeled.parquet`. Omit `labeling` (or set to `null`) to skip.

```yaml
labeling:
  - count               # corrected_count > 5
  - count_zscore        # corrected_count > 5 AND z_score > 1 (pre-supplied z-score only)
  - count_zscore_lib    # corrected_count > 5 AND (z_score_lib_normalized > 1 OR any disynthon z_score_lib_normalized > 1)    [3]
  - count_zscore_global # corrected_count > 5 AND (z_score_global_normalized > 1 OR any disynthon z_score_global_normalized > 1) [3]
  - count_polyo         # corrected_count > 5 AND (polyo > 4 OR any disynthon polyo > 4)                [4]
```

Each mode validates that its required columns are present and fails with a clear error if they are not. `z_score_lib_normalized`/`z_score_global_normalized` modes require singleton enrichment to have been computed (not applicable if a pre-supplied `z_score` was used without running `singleton.py`).

If a `SMILES` column is present and any SMILES appear more than once, duplicate rows are also written to `labeled_duplicates.parquet` sorted by SMILES.

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

## How the pipeline runs on Longleaf

`submit.slurm` launches a single lightweight SLURM job (8 GB, 1 CPU) that runs Nextflow as a coordinator. Nextflow then submits each pipeline process as its own separate SLURM job. The resource requirements for each process (CPUs, memory, time) are defined in the `longleaf` profile in `pipeline/nextflow.config` — not in `submit.slurm`.

## Tuning resources

Per-process resource settings can be adjusted in the `longleaf` profile in `pipeline/nextflow.config`.

## Dependencies

**Longleaf:**
- **Python 3.12.4** — `module load python/3.12.4`
- **Nextflow** — `module load nextflow`
- **fastp/1.0.1[1]** — `module load fastp/1.0.1` (loaded automatically by Nextflow on Longleaf)
- **DELi[2]** — installed into `.venv` by `setup.sh`; decoding processes in `pipeline/subworkflows/deli.nf` are adapted from [DELi's Nextflow workflow](https://github.com/Popov-Lab-UNC/DELi)

**Local Mac:**
- **Python 3.13** — required by DELi; managed automatically via `uv` in `setup_local.sh`
- **uv** — https://docs.astral.sh/uv/getting-started/installation/
- **Nextflow** — https://www.nextflow.io/docs/latest/install.html
- **fastp** — only needed for paired-end runs (`read_2` set); install via `brew install fastp`
- - **DELi[2]**

[1] Shifu Chen. 2025. fastp 1.0: An ultra-fast all-round tool for FASTQ data quality control and preprocessing. iMeta 2025: https://doi.org/10.1002/imt2.107

[2] Wellnitz J, Novy B, Maxfield T, Lin S-H, Zhilinskaya I, Axtman M, Leisner T, Merten E, Norris-Drouin JL, Hardy BP, Pearce KH, Popov KI. (2025). *Open-Source DNA-Encoded Library informatics Package for Design, Decoding, and Analysis: DELi*. bioRxiv. https://doi.org/10.1101/2025.02.25.640184

[3] Faver JC, Riehle K, Lancia DR Jr., Milbank JBJ, Kollmann CS, Simmons N, Yu Z, Matzuk MM. (2019). Quantitative Comparison of Enrichment from DNA-Encoded Chemical Library Selections. *ACS Combinatorial Science*, 21(2), 75–82. https://doi.org/10.1021/acscombsci.8b00116

[4] Chen Q, Li Y, Lin C, Chen L, Luo H, Xia S, Liu C, Cheng X, Liu C, Li J, Dou D. (2022). Expanding the DNA-encoded library toolbox: identifying small molecules targeting RNA. *Nucleic Acids Research*, 50(12), e67. https://doi.org/10.1093/nar/gkac173
