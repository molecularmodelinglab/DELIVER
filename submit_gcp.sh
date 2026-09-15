#!/usr/bin/env bash
# =============================================================
#  submit_gcp.sh  —  DELIVER pipeline on GCP Cloud Batch (FINAL runs)
#
#  Production counterpart of test_gcp.sh: named runs, gcp profile,
#  logs preserved to GCS AND locally, same flag flexibility.
#
#  Each run is identified by --run-name and gets isolated dirs:
#      gs://$BUCKET/deliver-runs/<name>/{work,results,inputs,logs}
#      runs/<name>/            (local: launcher log, derived params, traces)
#
#  Configuration is read from .env in this directory.
#  Required variables: PROJECT, REGION, BUCKET, CONTAINER_REGISTRY,
#                      SERVICE_ACCOUNT   (PARAMS_FILE optional,
#                      default gcp_params.yml)
#  NOTE: WORK_DIR/LOG_DIR from .env are no longer used — both are
#  derived from --run-name (override with --work-dir/--log-dir).
#
#  Usage:
#    bash submit_gcp.sh --run-name SGCDEL_Camp1_WDR91            # fresh run
#    bash submit_gcp.sh --resume                                 # resume last run
#    bash submit_gcp.sh --resume --run-name SGCDEL_Camp1_WDR91   # resume specific run
#
#  Flags:
#    --run-name <name>    REQUIRED for a fresh run; names all GCS/local dirs
#    --resume             resume (reuses runs/.last_run_name when --run-name absent)
#    --resume-id <uuid>   resume a SPECIFIC Nextflow session (implies --resume).
#                         Bare -resume picks the last entry in .nextflow/history,
#                         which is wrong if anything else ran from this directory
#                         since (another launch, or a repo sync that replaced the
#                         history file) — then the run silently starts over with
#                         all-new task hashes. Find the right UUID with:
#                           grep -m1 "Session UUID" <that run's .nextflow.log>
#                         (runs/<name>/ and gs://…/<name>/logs/ keep those logs)
#    --params-file <yml>  base params file (default: $PARAMS_FILE or gcp_params.yml)
#    --container <ref>    container image (default: CONTAINER_REGISTRY from .env)
#    --spot <true|false>  override params.spot (false = on-demand VMs, no preemption)
#    --chunk-size <n>     override splitFastq chunk size (default: params file value)
#    --read-1 <list>      comma-separated R1 file(s); local paths are uploaded
#    --read-2 <list>      comma-separated R2 file(s); omit for single-end
#    --merged-fastq <f>   pre-merged FASTQ — skips PREPROCESS (recovery/re-decode)
#    --keep-work          keep the GCS work dir even on success
#    --work-dir/--log-dir explicit overrides of the derived locations
#    --project/--bucket/--region   override .env values
# =============================================================
set -euo pipefail

DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load configuration from .env ──────────────────────────────
ENV_FILE="${DELIVER_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE" >&2
    echo "       Create one with PROJECT, REGION, BUCKET, CONTAINER_REGISTRY, SERVICE_ACCOUNT — see README." >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Defaults & argument parsing ───────────────────────────────
RUN_NAME="HitGen_WDR91_v0"
RESUME=false
RESUME_ID=""
KEEP_WORK=false
PARAMS_BASE="${PARAMS_FILE:-${DELIVER_DIR}/gcp_params.yml}"
CONTAINER="${CONTAINER_REGISTRY:-${REGION}-docker.pkg.dev/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:latest}"
READ1=""
READ2=""
MERGED_FASTQ=""
SPOT=""
CHUNK_SIZE=""
WORK_DIR_OVERRIDE=""
LOG_DIR_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-name)    RUN_NAME="$2";       shift 2 ;;
        --resume)      RESUME=true;         shift   ;;
        --resume-id)   RESUME=true; RESUME_ID="$2"; shift 2 ;;
        --params-file) PARAMS_BASE="$2";    shift 2 ;;
        --container)   CONTAINER="$2";      shift 2 ;;
        --spot)        SPOT="$2";           shift 2 ;;
        --chunk-size)  CHUNK_SIZE="$2";     shift 2 ;;
        --read-1)      READ1="$2";          shift 2 ;;
        --read-2)      READ2="$2";          shift 2 ;;
        --merged-fastq) MERGED_FASTQ="$2";  shift 2 ;;
        --keep-work)   KEEP_WORK=true;      shift   ;;
        --work-dir)    WORK_DIR_OVERRIDE="$2"; shift 2 ;;
        --log-dir)     LOG_DIR_OVERRIDE="$2";  shift 2 ;;
        --project)     PROJECT="$2";        shift 2 ;;
        --bucket)      BUCKET="$2";         shift 2 ;;
        --region)      REGION="$2";         shift 2 ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# Resolve PARAMS_BASE relative to repo root if it isn't absolute
if [[ "$PARAMS_BASE" != /* && "$PARAMS_BASE" != gs://* ]]; then
    PARAMS_BASE="${DELIVER_DIR}/${PARAMS_BASE}"
fi

# ── Validate required variables & flags ───────────────────────
missing=()
for var in PROJECT REGION BUCKET SERVICE_ACCOUNT; do
    [[ -z "${!var:-}" ]] && missing+=("$var")
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: required variable(s) not set in .env or via CLI: ${missing[*]}" >&2
    exit 1
fi
if [[ -z "$CONTAINER" ]]; then
    echo "ERROR: no container image — set CONTAINER_REGISTRY in .env or pass --container" >&2
    exit 1
fi
if [[ ! -f "$PARAMS_BASE" ]]; then
    echo "ERROR: params file not found: $PARAMS_BASE" >&2
    exit 1
fi
if [[ -n "$SPOT" && "$SPOT" != "true" && "$SPOT" != "false" ]]; then
    echo "ERROR: --spot must be 'true' or 'false' (got: $SPOT)" >&2
    exit 1
fi
if [[ -n "$MERGED_FASTQ" && -n "$READ1$READ2" ]]; then
    echo "ERROR: --merged-fastq cannot be combined with --read-1/--read-2" >&2
    exit 1
fi

# ── Run name: required for fresh runs, remembered for --resume ─
RUNS_DIR="${DELIVER_DIR}/runs"
mkdir -p "$RUNS_DIR"
LAST_NAME_FILE="${RUNS_DIR}/.last_run_name"
if [[ -z "$RUN_NAME" ]]; then
    if $RESUME && [[ -f "$LAST_NAME_FILE" ]]; then
        RUN_NAME="$(cat "$LAST_NAME_FILE")"
    else
        echo "ERROR: --run-name is required (e.g. --run-name SGCDEL_Camp1_WDR91)" >&2
        exit 1
    fi
fi
if ! $RESUME && [[ -d "${RUNS_DIR}/${RUN_NAME}" ]]; then
    echo "ERROR: runs/${RUN_NAME} already exists. To continue it: --resume --run-name ${RUN_NAME}" >&2
    echo "       For a fresh run pick a new name (or remove runs/${RUN_NAME} and its GCS dirs first)." >&2
    exit 1
fi
echo "$RUN_NAME" > "$LAST_NAME_FILE"

# ── Derived locations (override with --work-dir/--log-dir) ────
GCS_BASE="gs://${BUCKET}/deliver-runs/${RUN_NAME}"
WORK_URI="${WORK_DIR_OVERRIDE:-${GCS_BASE}/work}"
OUT_URI="${GCS_BASE}/results"
INPUT_URI="${GCS_BASE}/inputs"
RUN_DIR="${LOG_DIR_OVERRIDE:-${RUNS_DIR}/${RUN_NAME}}"
mkdir -p "$RUN_DIR"

# ── Log locally AND (at the end) to GCS ───────────────────────
LOG_FILE="${RUN_DIR}/launcher.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo "  DELIVER — GCP Cloud Batch FINAL run"
echo "  $(date)"
echo "  run name  : $RUN_NAME"
echo "  project   : $PROJECT"
echo "  region    : $REGION"
echo "  container : $CONTAINER"
echo "  work dir  : $WORK_URI"
echo "  results   : $OUT_URI"
echo "  logs      : $RUN_DIR  +  ${GCS_BASE}/logs/"
echo "  base params: $PARAMS_BASE"
echo "  resume    : $RESUME${RESUME_ID:+ (session $RESUME_ID)}"
echo "============================================================"

# ── Pin the Nextflow version ──────────────────────────────────
# Same behavior on the Mac and the launcher VM; newer Nextflow (26.x)
# rejects the `def sizeMem(...)` helpers in nextflow.config.
export NXF_VER="${NXF_VER:-25.10.4}"

# ── Pre-flight checks ─────────────────────────────────────────
for tool in nextflow java gcloud python3; do
    command -v "$tool" &>/dev/null || { echo "ERROR: '$tool' not found in PATH" >&2; exit 1; }
done
if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "ERROR: no GCP Application Default Credentials — run: gcloud auth application-default login" >&2
    exit 1
fi
# Object-level check (storage.objects.list), NOT `buckets describe`:
# the latter needs storage.buckets.get, which the VM's service account
# doesn't have — the pipeline itself only reads/writes objects.
if ! gcloud storage ls "gs://$BUCKET" --project "$PROJECT" &>/dev/null; then
    echo "ERROR: bucket gs://$BUCKET not accessible in project $PROJECT" >&2
    exit 1
fi
echo "[preflight] tools + credentials + bucket OK ✓"

# ── Activate venv (for python3+pyyaml) ────────────────────────
if [[ -f "${DELIVER_DIR}/.venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${DELIVER_DIR}/.venv/bin/activate"
fi

# ── Upload local read files (if any) and build gs:// lists ────
resolve_reads() {
    # comma-separated list; local files are uploaded to $INPUT_URI
    local list="$1" out=() f
    IFS=',' read -ra items <<< "$list"
    for f in "${items[@]}"; do
        f="$(echo "$f" | xargs)"   # trim
        if [[ "$f" == gs://* ]]; then
            out+=("$f")
        else
            [[ -f "$f" ]] || { echo "ERROR: read file not found: $f" >&2; exit 1; }
            gcloud storage cp "$f" "${INPUT_URI}/" --project "$PROJECT" 1>&2
            out+=("${INPUT_URI}/$(basename "$f")")
        fi
    done
    (IFS=','; echo "${out[*]}")
}

R1_URIS=""
R2_URIS=""
MERGED_URI=""
if [[ -n "$READ1" ]]; then
    echo "[inputs] staging R1 file(s) …"
    R1_URIS="$(resolve_reads "$READ1")"
    if [[ -n "$READ2" ]]; then
        echo "[inputs] staging R2 file(s) …"
        R2_URIS="$(resolve_reads "$READ2")"
    fi
elif [[ -n "$READ2" ]]; then
    echo "ERROR: --read-2 given without --read-1" >&2
    exit 1
fi
if [[ -n "$MERGED_FASTQ" ]]; then
    echo "[inputs] staging pre-merged FASTQ …"
    MERGED_URI="$(resolve_reads "$MERGED_FASTQ")"
fi

# ── Derive the run params file from the base params ───────────
# Unlike test_gcp.sh, counts mode is preserved: the base file's entry
# points are only overridden when --read-1/--merged-fastq are given.
DERIVED_PARAMS="${RUN_DIR}/params_run.yml"
python3 - "$PARAMS_BASE" "$DERIVED_PARAMS" "$OUT_URI" "$CHUNK_SIZE" "$R1_URIS" "$R2_URIS" "$MERGED_URI" "$SPOT" <<'EOF'
import sys
import yaml

base_file, out_file, out_dir, chunk_size, r1, r2, merged, spot = sys.argv[1:9]
params = yaml.safe_load(open(base_file))
if r1:
    params["read_1"] = r1.split(",")
    # never mix caller-supplied R1 with the base file's R2
    params["read_2"] = r2.split(",") if r2 else None
    params["merged_fastq"] = None
    params["counts"] = None
if merged:
    params["merged_fastq"] = merged
    params["read_1"] = None
    params["read_2"] = None
    params["counts"] = None
if spot:
    params["spot"] = (spot == "true")
if chunk_size:
    params["chunk_size"] = int(chunk_size)
params["out_dir"] = out_dir
with open(out_file, "w") as fh:
    yaml.safe_dump(params, fh, sort_keys=False)
print(f"[params] wrote {out_file}")
for k in ("read_1", "read_2", "merged_fastq", "counts", "out_dir", "chunk_size", "spot"):
    if params.get(k) is not None:
        print(f"[params] {k}: {params[k]}")
EOF

# ── Export DELI_DATA_DIR from the derived params ──────────────
export DELI_DATA_DIR
DELI_DATA_DIR=$(python3 -c \
    "import yaml; print(yaml.safe_load(open('${DERIVED_PARAMS}')).get('deli_data_dir', '') or '')")
[[ -z "$DELI_DATA_DIR" ]] && echo "WARNING: deli_data_dir not set in $DERIVED_PARAMS" >&2

RESUME_FLAG=""
$RESUME && RESUME_FLAG="-resume"
# explicit session id (expands unquoted below to: -resume <uuid>)
[[ -n "$RESUME_ID" ]] && RESUME_FLAG="-resume ${RESUME_ID}"

# ── Run the pipeline ──────────────────────────────────────────
cd "$DELIVER_DIR"
echo ""
echo "[nextflow] starting pipeline (profile: gcp) …"
echo ""
set +e
nextflow run pipeline/main.nf \
    $RESUME_FLAG \
    -profile gcp \
    -params-file "$DERIVED_PARAMS" \
    -work-dir "$WORK_URI" \
    --project "$PROJECT" \
    --bucket  "$BUCKET" \
    --region  "$REGION" \
    --container "$CONTAINER" \
    --service_account "$SERVICE_ACCOUNT" \
    --log_dir "$RUN_DIR"
EXIT_CODE=$?
set -e

# ── Preserve logs to GCS (success or failure) ─────────────────
echo ""
echo "[logs] copying launcher log, nextflow log, and traces to ${GCS_BASE}/logs/ …"
gcloud storage cp \
    "$LOG_FILE" \
    "${DELIVER_DIR}/.nextflow.log" \
    "$RUN_DIR"/execution_trace_*.txt \
    "$RUN_DIR"/execution_report_*.html \
    "$DERIVED_PARAMS" \
    "${GCS_BASE}/logs/" --project "$PROJECT" 2>/dev/null \
    && echo "[logs] preserved to GCS ✓" \
    || echo "WARNING: some log files could not be copied to GCS" >&2

# ── Post-run report ───────────────────────────────────────────
echo ""
echo "============================================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "  RUN ${RUN_NAME} COMPLETED ✓"
    echo ""
    echo "[results] published outputs in $OUT_URI :"
    gcloud storage ls -l "$OUT_URI/**" --project "$PROJECT" 2>/dev/null || true
    echo ""
    TRACE_FILE=$(ls -t "$RUN_DIR"/execution_trace_*.txt 2>/dev/null | head -1 || true)
    if [[ -n "$TRACE_FILE" ]]; then
        echo "[trace] decode-stage tasks (from $(basename "$TRACE_FILE")):"
        head -1 "$TRACE_FILE"
        grep -E "DecodeChunk|CollectDecodeChunks|CountChunk|CollectCountChunks" "$TRACE_FILE" || true
    fi
    if ! $KEEP_WORK; then
        echo ""
        echo "[cleanup] removing work dir …"
        gcloud storage rm --recursive "$WORK_URI" --project "$PROJECT" 2>/dev/null \
            && echo "[cleanup] work dir deleted ✓" \
            || echo "WARNING: could not delete $WORK_URI" >&2
    else
        echo "[cleanup] --keep-work: work dir preserved at $WORK_URI"
    fi
else
    echo "  RUN ${RUN_NAME} FAILED (exit code $EXIT_CODE) ✗" >&2
    echo "  work dir preserved for debugging: $WORK_URI" >&2
    echo "  resume with: bash submit_gcp.sh --resume --run-name ${RUN_NAME}" >&2
fi
echo "============================================================"
exit $EXIT_CODE
