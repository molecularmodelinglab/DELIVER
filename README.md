# DELIVER

Nextflow pipeline for DEL (DNA Encoded Library) data processing.

**We are using the "patch" branch of DELi as of now:** https://github.com/Popov-Lab-UNC/DELi/tree/patch

## Quick start — Longleaf HPC

```bash
# One-time setup on login node
bash setup.sh
```

Edit `params.yml` (see [parameter reference](#paramsyml) below), then submit. Each pipeline step runs as a separate SLURM job — see [How the pipeline runs on Longleaf](#how-the-pipeline-runs-on-longleaf) for details.

```bash
sbatch submit.slurm \
  --work-dir    /path/to/work \
  --params-file /path/to/DELIVER/params.yml \
  --log-dir     /path/to/logs
```

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
├── params.yml                        # template — copy to params_local.yml for local runs
├── setup.sh                          # one-time setup for Longleaf: creates .venv, installs DELi
├── setup_local.sh                    # one-time setup for local Mac (uses uv + Python 3.13)
├── submit.slurm                      # SLURM launcher for Longleaf
├── run_local.sh                      # run script for local Mac
├── pipeline/
│   ├── main.nf                       # auto-detects mode from params
│   ├── nextflow.config               # longleaf / local profiles
│   └── subworkflows/
│       ├── preprocess.nf             # CONCAT + FASTP_MERGE (paired-end merge)
│       ├── deli.nf                   # DELi processes + DELI workflow
│       └── postprocess.nf            # BUILD_LIBRARY_DICT + DEDUPLICATE + ENRICHMENT workflows
├── src/
│   └── deliver/
│       └── postprocess/              # standalone Click CLI scripts called by NF
│           ├── build_library_dict.py # build library dictionary JSON from DELi data
│           ├── deduplicate.py        # deduplication + aggregation (TODO)
│           └── enrichment.py         # enrichment scoring (TODO)
└── scripts/
    └── convert_hitgen/               # Hitgen TSV → DELi format converter
```

## Vendor data preparation

Before running the pipeline you need DELi-format library definitions. If your libraries come from **Hitgen**, use the conversion script:

```bash
sbatch scripts/convert_hitgen/convert_hitgen.slurm \
  --input-dir  /path/to/hitgen/tsv_files \
  --output-dir /path/to/deli_data \
  --config     scripts/convert_hitgen/library_config.yml
```

This creates `libraries/` and `building_blocks/` inside `--output-dir`, which you then point `deli_data_dir` at in `params.yml`. See [scripts/convert_hitgen/README.md](scripts/convert_hitgen/README.md) for setup and input format details.

## Pipeline stages

| Stage | Status |
|-------|--------|
| Preprocessing: concat lanes, merge paired-end reads (fastp) | implemented |
| DELi decoding: chunk → decode → collect → count → summarize → report | implemented |
| Build library dictionary (library_dict.json) | implemented |
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

[2]Wellnitz J, Novy B, Maxfield T, Lin S-H, Zhilinskaya I, Axtman M, Leisner T, Merten E, Norris-Drouin JL, Hardy BP, Pearce KH, Popov KI. (2025). *Open-Source DNA-Encoded Library informatics Package for Design, Decoding, and Analysis: DELi*. bioRxiv. https://doi.org/10.1101/2025.02.25.640184
