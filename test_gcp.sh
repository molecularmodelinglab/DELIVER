#!/usr/bin/env bash
# =============================================================
#  test_gcp.sh  —  small-file test run of DELIVER on GCP Cloud Batch
#
#  Purpose: validate a DELi/DELIVER change (e.g. the streaming-collect
#  DELi fix) end-to-end on GCP using SMALL input files, without touching
#  production output locations. Each invocation gets its own run tag with
#  isolated work/results dirs under gs://$BUCKET/deliver-test/.
#
#  Configuration comes from .env (same variables as submit_gcp.sh).
#
#  Usage:
#    bash test_gcp.sh                                  # reads from gcp_params.yml
#    bash test_gcp.sh --build                          # build+push test image from
#                                                      #   local DELi first, then run
#    bash test_gcp.sh --read-1 r1.fastq.gz --read-2 r2.fastq.gz
#    bash test_gcp.sh --read-1 gs://bucket/small_R1.fastq.gz
#    bash test_gcp.sh --resume                         # resume the previous test run
#
#  Flags:
#    --build              build & push a test image from the local DELi clone
#                         (--deli-dir, default ../DELi-streaming-fix) tagged
#                         deli-streaming-test, and use it for this run
#    --deli-dir <path>    local DELi clone for --build
#    --container <ref>    use this container image (default: CONTAINER_REGISTRY
#                         from .env, or the test image when --build is given)
#    --params-file <yml>  base params file (default: gcp_params.yml)
#    --read-1 <list>      comma-separated R1 file(s); local paths are uploaded
#                         to gs://$BUCKET/deliver-test/<tag>/inputs/
#    --read-2 <list>      comma-separated R2 file(s); omit for single-end
#    --merged-fastq <f>   pre-merged FASTQ (skips PREPROCESS, goes straight to
#                         decode) — e.g. a FASTP_MERGE output from an old work
#                         dir; local paths are uploaded like --read-1
#    --spot <true|false>  override params.spot: false = on-demand VMs (no
#                         preemption; use for recovery resumes with few long
#                         serial tasks left)
#    --chunk-size <n>     splitFastq chunk size (default: 250000, small so even
#                         small files produce several DecodeChunk/collect inputs)
#    --resume             resume the most recent test run (reuses its tag/dirs)
#    --no-resume          start a FRESH run with a new tag (RESUME currently
#                         defaults to true) — required for side-by-side
#                         comparison runs, e.g. --container …:deli-patch
#    --keep-work          keep the GCS work dir even on success
# =============================================================
set -euo pipefail

DELIVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load configuration from .env ──────────────────────────────
ENV_FILE="${DELIVER_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE (see README for required variables)" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Defaults & argument parsing ───────────────────────────────
BUILD=false
RESUME=false
KEEP_WORK=false
PARAMS_BASE="${DELIVER_DIR}/gcp_params.yml"
READ1=""
READ2=""
MERGED_FASTQ=""
SPOT=""
CHUNK_SIZE=1500000
# explicit CONTAINER_REGISTRY wins; else derive from REPO_NAME so runs
# automatically track the repo that build_and_push.sh pushes to
CONTAINER="${CONTAINER_REGISTRY:-${REGION}-docker.pkg.dev/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:latest}"
DELI_LOCAL_DIR="${DELI_LOCAL_DIR:-${DELIVER_DIR}/../DELi-streaming-fix}"
TEST_TAG_NAME="deli-streaming-test"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)       BUILD=true;       shift   ;;
        --deli-dir)    DELI_LOCAL_DIR="$2"; shift 2 ;;
        --container)   CONTAINER="$2";   shift 2 ;;
        --params-file) PARAMS_BASE="$2"; shift 2 ;;
        --read-1)      READ1="$2";       shift 2 ;;
        --read-2)      READ2="$2";       shift 2 ;;
        --merged-fastq) MERGED_FASTQ="$2"; shift 2 ;;
        --spot)        SPOT="$2";        shift 2 ;;
        --chunk-size)  CHUNK_SIZE="$2";  shift 2 ;;
        --resume)      RESUME=true;      shift   ;;
        --no-resume)   RESUME=false;     shift   ;;
        --keep-work)   KEEP_WORK=true;   shift   ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# ── Validate required .env variables ──────────────────────────
missing=()
for var in PROJECT REGION BUCKET SERVICE_ACCOUNT; do
    [[ -z "${!var:-}" ]] && missing+=("$var")
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: required variable(s) not set in .env: ${missing[*]}" >&2
    exit 1
fi
if [[ ! -f "$PARAMS_BASE" ]]; then
    echo "ERROR: params file not found: $PARAMS_BASE" >&2
    exit 1
fi

# ── Optionally build+push a test image from the local DELi ────
if $BUILD; then
    if [[ -z "${CONTAINER_REGISTRY:-}" ]]; then
        echo "ERROR: --build needs CONTAINER_REGISTRY in .env (to derive the test image ref)" >&2
        exit 1
    fi
    CONTAINER="${CONTAINER_REGISTRY%:*}:${TEST_TAG_NAME}"
    echo "[build] Building test image from ${DELI_LOCAL_DIR} → ${CONTAINER}"
    "${DELIVER_DIR}/build_and_push.sh" --deli-dir "$DELI_LOCAL_DIR" --tag "$TEST_TAG_NAME"
fi
if [[ -z "$CONTAINER" ]]; then
    echo "ERROR: no container image — set CONTAINER_REGISTRY in .env, or pass --container/--build" >&2
    exit 1
fi

# ── Run tag: new per invocation, reused on --resume ───────────
TEST_RUNS_DIR="${DELIVER_DIR}/test_runs_sgc"
mkdir -p "$TEST_RUNS_DIR"
LAST_TAG_FILE="${TEST_RUNS_DIR}/.last_run_tag"
if $RESUME; then
    if [[ ! -f "$LAST_TAG_FILE" ]]; then
        echo "ERROR: --resume but no previous test run recorded in $LAST_TAG_FILE" >&2
        exit 1
    fi
    RUN_TAG="$(cat "$LAST_TAG_FILE")"
else
    RUN_TAG="test_$(date +%Y%m%d_%H%M%S)"
    echo "$RUN_TAG" > "$LAST_TAG_FILE"
fi

RUN_DIR="${TEST_RUNS_DIR}/${RUN_TAG}"          # local: logs, derived params, trace
GCS_BASE="gs://${BUCKET}/deliver-test-WDR91/${RUN_TAG}"
WORK_URI="${GCS_BASE}/work"
OUT_URI="${GCS_BASE}/results"
INPUT_URI="${GCS_BASE}/inputs"
mkdir -p "$RUN_DIR"

LOG_FILE="${RUN_DIR}/launcher.log"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "============================================================"
echo "  DELIVER — GCP small-file TEST run"
echo "  $(date)"
echo "  run tag   : $RUN_TAG"
echo "  project   : $PROJECT"
echo "  region    : $REGION"
echo "  container : $CONTAINER"
echo "  work dir  : $WORK_URI"
echo "  results   : $OUT_URI"
echo "  base params: $PARAMS_BASE"
echo "  chunk_size : $CHUNK_SIZE"
echo "  resume    : $RESUME"
echo "============================================================"

# ── Pin the Nextflow version ──────────────────────────────────
# The head job must behave identically on the Mac and the launcher VM.
# A freshly installed Nextflow (26.x on the VM) defaults to the strict
# config parser, which rejects the `def sizeMem(...)` helpers in
# nextflow.config ("Unexpected input: '('"). The nextflow launcher
# respects NXF_VER and auto-downloads the pinned version on first use.
export NXF_VER="${NXF_VER:-25.10.4}"

# ── Pre-flight checks ─────────────────────────────────────────
for tool in nextflow java gcloud python3; do
    command -v "$tool" &>/dev/null || { echo "ERROR: '$tool' not found in PATH" >&2; exit 1; }
done
if ! gcloud auth application-default print-access-token &>/dev/null; then
    echo "ERROR: no GCP Application Default Credentials — run: gcloud auth application-default login" >&2
    exit 1
fi
# Object-level check (storage.objects.list), NOT `buckets describe`: the
# latter needs storage.buckets.get, which service accounts with object-only
# roles (e.g. the VM launcher's SA) don't have — the pipeline itself only
# ever reads/writes objects.
if ! gcloud storage ls "gs://$BUCKET" --project "$PROJECT" &>/dev/null; then
    echo "ERROR: bucket gs://$BUCKET not accessible in project $PROJECT" >&2
    exit 1
fi
echo "[preflight] tools + credentials + bucket OK ✓"

# ── Activate venv (for python3+pyyaml), like submit_gcp.sh ────
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

if [[ -n "$SPOT" && "$SPOT" != "true" && "$SPOT" != "false" ]]; then
    echo "ERROR: --spot must be 'true' or 'false' (got: $SPOT)" >&2
    exit 1
fi
if [[ -n "$MERGED_FASTQ" && -n "$READ1$READ2" ]]; then
    echo "ERROR: --merged-fastq cannot be combined with --read-1/--read-2" >&2
    exit 1
fi

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

# ── Derive the test params file from the base params ──────────
DERIVED_PARAMS="${RUN_DIR}/params_test.yml"
python3 - "$PARAMS_BASE" "$DERIVED_PARAMS" "$OUT_URI" "$CHUNK_SIZE" "$R1_URIS" "$R2_URIS" "$MERGED_URI" "$SPOT" <<'EOF'
import sys
import yaml

base_file, out_file, out_dir, chunk_size, r1, r2, merged, spot = sys.argv[1:9]
params = yaml.safe_load(open(base_file))
if r1:
    params["read_1"] = r1.split(",")
    # never mix caller-supplied R1 with the base file's R2
    params["read_2"] = r2.split(",") if r2 else None
if merged:
    # merged_fastq mode is exclusive: null the other entry points
    params["merged_fastq"] = merged
    params["read_1"] = None
    params["read_2"] = None
if spot:
    params["spot"] = (spot == "true")
params["counts"] = None
params["out_dir"] = out_dir
params["chunk_size"] = int(chunk_size)
with open(out_file, "w") as fh:
    yaml.safe_dump(params, fh, sort_keys=False)
print(f"[params] wrote {out_file}")
print(f"[params] read_1: {params.get('read_1')}")
print(f"[params] read_2: {params.get('read_2')}")
if params.get("merged_fastq"):
    print(f"[params] merged_fastq: {params['merged_fastq']}")
if "spot" in params:
    print(f"[params] spot: {params['spot']}")
EOF

# ── Export DELI_DATA_DIR (mirrors submit_gcp.sh) ──────────────
export DELI_DATA_DIR
DELI_DATA_DIR=$(python3 -c \
    "import yaml; print(yaml.safe_load(open('${DERIVED_PARAMS}')).get('deli_data_dir', '') or '')")

RESUME_FLAG=""
$RESUME && RESUME_FLAG="-resume"

# ── Run the pipeline ──────────────────────────────────────────
cd "$DELIVER_DIR"
echo ""
echo "[nextflow] starting test pipeline …"
echo ""
set +e
nextflow run pipeline/main.nf \
    $RESUME_FLAG \
    -profile gcp_test \
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

# ── Post-run report ───────────────────────────────────────────
echo ""
echo "============================================================"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "  TEST RUN PASSED ✓"
    echo ""
    echo "[results] published outputs in $OUT_URI :"
    gcloud storage ls -l "$OUT_URI/**" --project "$PROJECT" 2>/dev/null || true
    echo ""
    # Decode-stage resource evidence from the execution trace (peak_rss of
    # CollectDecodeChunks is the number the streaming-collect fix bounds).
    TRACE_FILE=$(ls -t "$RUN_DIR"/execution_trace_*.txt 2>/dev/null | head -1 || true)
    if [[ -n "$TRACE_FILE" ]]; then
        echo "[trace] decode-stage tasks (from $(basename "$TRACE_FILE")):"
        head -1 "$TRACE_FILE"
        grep -E "DecodeChunk|CollectDecodeChunks|CountChunk|CollectCountChunks" "$TRACE_FILE" || true
    fi
    if ! $KEEP_WORK; then
        echo ""
        echo "[cleanup] removing test work dir …"
        gcloud storage rm --recursive "$WORK_URI" --project "$PROJECT" 2>/dev/null \
            && echo "[cleanup] work dir deleted ✓" \
            || echo "WARNING: could not delete $WORK_URI" >&2
    else
        echo "[cleanup] --keep-work: work dir preserved at $WORK_URI"
    fi
else
    echo "  TEST RUN FAILED (exit code $EXIT_CODE) ✗" >&2
    echo "  work dir preserved for debugging: $WORK_URI" >&2
    echo "  launcher log: $LOG_FILE" >&2
fi
echo "============================================================"
exit $EXIT_CODE
