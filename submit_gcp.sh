#!/usr/bin/env bash
# =============================================================
#  submit_gcp.sh  —  DELIVER pipeline on GCP Cloud Batch
#
#  Drop-in replacement for submit.slurm.
#  Requires: nextflow, gcloud CLI, python3 (with pyyaml), java
#
#  Configuration is read from .env in this directory.
#  Required variables (see .env):
#    PROJECT, REGION, BUCKET, WORK_DIR, LOG_DIR, PARAMS_FILE,
#    CONTAINER_REGISTRY, SERVICE_ACCOUNT
#
#  CLI flags override values from .env:
#    --work-dir, --params-file, --log-dir, --project, --bucket,
#    --region, --resume
# =============================================================
set -euo pipefail

# ── Resolve the directory this script lives in ────────────────
DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load configuration from .env ──────────────────────────────
# All GCP variables live in .env (gitignored). CLI flags below
# can still override any value.
ENV_FILE="${DELIVER_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE" >&2
    echo "       Create one with PROJECT, REGION, BUCKET, WORK_DIR, LOG_DIR," >&2
    echo "       PARAMS_FILE, CONTAINER_REGISTRY, SERVICE_ACCOUNT — see README." >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

RESUME=false

# ── Argument parsing (overrides values from .env) ─────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --work-dir)    WORK_DIR="$2";    shift 2 ;;
        --params-file) PARAMS_FILE="$2"; shift 2 ;;
        --log-dir)     LOG_DIR="$2";     shift 2 ;;
        --project)     PROJECT="$2";     shift 2 ;;
        --bucket)      BUCKET="$2";      shift 2 ;;
        --region)      REGION="$2";      shift 2 ;;
        --resume)      RESUME=true;      shift   ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Resolve PARAMS_FILE relative to repo root if it isn't absolute or gs://
if [[ -n "${PARAMS_FILE:-}" && "$PARAMS_FILE" != /* && "$PARAMS_FILE" != gs://* ]]; then
    PARAMS_FILE="${DELIVER_DIR}/${PARAMS_FILE}"
fi

# ── Validate required variables ───────────────────────────────
missing=()
for var in PROJECT REGION BUCKET WORK_DIR LOG_DIR PARAMS_FILE CONTAINER_REGISTRY SERVICE_ACCOUNT; do
    if [[ -z "${!var:-}" ]]; then
        missing+=("$var")
    fi
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: required variable(s) not set in .env or via CLI: ${missing[*]}" >&2
    exit 1
fi

# ── Validate params file exists ───────────────────────────────
if [[ ! -f "$PARAMS_FILE" ]]; then
    echo "ERROR: params file not found: $PARAMS_FILE" >&2
    exit 1
fi

# ── Create log dir and redirect all output to a log file ──────
# (mirrors the exec > ... redirect in submit.slurm)
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/launcher_$(date +%Y%m%d_%H%M%S).log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo "  DELIVER — GCP Cloud Batch Launcher"
echo "  $(date)"
echo "  project   : $PROJECT"
echo "  bucket    : gs://$BUCKET"
echo "  region    : $REGION"
echo "  work-dir  : $WORK_DIR"
echo "  params    : $PARAMS_FILE"
echo "  log       : $LOG_FILE"
echo "  resume    : $RESUME"
echo "  container-registry    : $CONTAINER_REGISTRY"
echo "  Service account email    : $SERVICE_ACCOUNT"
echo "============================================================"

# ── Pre-flight checks ─────────────────────────────────────────
for tool in nextflow java gcloud python3; do
    if ! command -v "$tool" &>/dev/null; then
        echo "ERROR: '$tool' not found in PATH" >&2
        exit 1
    fi
done
echo "[preflight] All required tools found ✓"

# Verify GCP credentials
if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "ERROR: No GCP Application Default Credentials found." >&2
    echo "       Run: gcloud auth application-default login" >&2
    exit 1
fi
echo "[preflight] GCP credentials OK ✓"

# Verify GCS bucket is accessible
if ! gcloud storage buckets describe "gs://$BUCKET" --project "$PROJECT" &>/dev/null; then
    echo "ERROR: GCS bucket gs://$BUCKET not accessible in project $PROJECT" >&2
    exit 1
fi
echo "[preflight] GCS bucket accessible ✓"

# ── Move into the DELIVER repo directory ─────────────────────
cd "$DELIVER_DIR" || exit 1

# ── Activate virtualenv (same as HPC version) ─────────────────
if [[ -f "${DELIVER_DIR}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${DELIVER_DIR}/.venv/bin/activate"
    echo "[setup] Python venv activated ✓"
else
    echo "WARNING: .venv not found at ${DELIVER_DIR}/.venv — using system python3" >&2
fi

# ── Export DELI_DATA_DIR from params file ─────────────────────
# (identical logic to submit.slurm)
export DELI_DATA_DIR
DELI_DATA_DIR=$(python3 -c \
    "import yaml; print(yaml.safe_load(open('${PARAMS_FILE}')).get('deli_data_dir', '') or '')")

if [[ -z "$DELI_DATA_DIR" ]]; then
    echo "WARNING: deli_data_dir not set in $PARAMS_FILE" >&2
else
    echo "[setup] DELI_DATA_DIR=$DELI_DATA_DIR"
fi

# ── Build resume flag ─────────────────────────────────────────
RESUME_FLAG=""
$RESUME && RESUME_FLAG="-resume"

# ── Run the pipeline ──────────────────────────────────────────
echo ""
echo "[nextflow] Starting pipeline …"
echo ""


nextflow run pipeline/main.nf \
    $RESUME_FLAG \
    -profile gcp_test \
    --work_dir "$WORK_DIR" \
    -params-file "$PARAMS_FILE" \
    --project "$PROJECT" \
    --bucket  "$BUCKET" \
    --region  "$REGION" \
    --container "$CONTAINER_REGISTRY" \
    --service_account "$SERVICE_ACCOUNT"

EXIT_CODE=$?

echo ""
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[done] Pipeline completed successfully ✓"
    echo "[cleanup] Removing Nextflow work directory from GCS …"
    gcloud storage rm --recursive "$WORK_DIR" --project "$PROJECT" 2>/dev/null \
        && echo "[cleanup] Work directory deleted ✓" \
        || echo "WARNING: Could not delete work directory $WORK_DIR" >&2
else
    echo "[done] Pipeline FAILED (exit code $EXIT_CODE)" >&2
    echo "       Work directory preserved for debugging: $WORK_DIR"
fi

exit $EXIT_CODE



# nextflow run pipeline/gcp_sanity_check.nf \
# -c pipeline/nextflow.config \
# -profile gcp \
# -w gcp_work bucket \
# --project GCP project \
# --bucket  work bucket to store result \
# --region  region