#!/usr/bin/env bash
# =============================================================
#  build_and_push.sh
#
#  Builds the DELIVER Docker image and pushes it to GCP
#  Artifact Registry. Run this once (or on each code change)
#  from the root of the DELIVER repository.
#
#  Configuration is read from .env in this directory.
#  Required variables (see .env):
#    PROJECT, REGION, REPO_NAME, IMAGE_NAME, TAG
#
#  CLI flags override values from .env:
#    --project, --region, --tag, --deli-dir, --github, --no-latest
#
#  DELi source selection (staged into .deli-src/ for the Dockerfile):
#    default              — GitHub Popov-Lab-UNC/DELi @ DELI_REF (.env, default "patch")
#    --deli-dir <path>    — a local DELi git clone (its checked-out branch's
#                           committed state), e.g. ../DELi-streaming-fix to
#                           test the streaming-collect fix on GCP
#    --github             — force the GitHub @ DELI_REF path even when
#                           DELI_LOCAL_DIR is set (baseline/comparison builds)
#    DELI_LOCAL_DIR (.env) — same as --deli-dir, as a persistent setting
#
#  --no-latest: tag/push ONLY :$TAG, leaving :latest untouched. Use for
#    side-by-side comparison images (e.g. upstream patch DELi vs the
#    streaming-collect fix) so runs pinned to :latest are not disturbed.
#
#  Usage:
#    chmod +x build_and_push.sh
#    ./build_and_push.sh                                      # uses .env
#    ./build_and_push.sh --tag 1.0.0                          # override tag
#    ./build_and_push.sh --project my-proj --region us-east1  # override more
#    ./build_and_push.sh --deli-dir ../DELi-streaming-fix --tag deli-streaming-test
#    ./build_and_push.sh --github --tag deli-patch --no-latest   # upstream-patch comparison image
# =============================================================
set -euo pipefail

# ── Resolve the directory this script lives in ────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── Load configuration from .env ──────────────────────────────
ENV_FILE="${SCRIPT_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: .env not found at $ENV_FILE" >&2
    echo "       Create one with PROJECT, REGION, REPO_NAME, IMAGE_NAME, TAG — see README." >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# ── Argument parsing (overrides values from .env) ─────────────
PUSH_LATEST=true
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project)   PROJECT="$2";        shift 2 ;;
        --region)    REGION="$2";         shift 2 ;;
        --tag)       TAG="$2";            shift 2 ;;
        --deli-dir)  DELI_LOCAL_DIR="$2"; shift 2 ;;
        --github)    DELI_LOCAL_DIR="";   shift   ;;
        --no-latest) PUSH_LATEST=false;   shift   ;;
        *) echo "Unknown argument: $1" >&2; exit 1 ;;
    esac
done

# ── Validate required variables ───────────────────────────────
missing=()
for var in PROJECT REGION REPO_NAME IMAGE_NAME TAG; do
    if [[ -z "${!var:-}" ]]; then
        missing+=("$var")
    fi
done
if (( ${#missing[@]} > 0 )); then
    echo "ERROR: required variable(s) not set in .env or via CLI: ${missing[*]}" >&2
    exit 1
fi

REGISTRY="${REGION}-docker.pkg.dev"
FULL_IMAGE="${REGISTRY}/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:${TAG}"

echo "============================================================"
echo "  DELIVER — Docker Build & Push"
echo "  project  : $PROJECT"
echo "  region   : $REGION"
echo "  image    : $FULL_IMAGE"
echo "============================================================"

# ── Step 1: Enable Artifact Registry API ─────────────────────
echo ""
echo "[1/5] Enabling Artifact Registry API …"
gcloud services enable artifactregistry.googleapis.com --project "$PROJECT"
echo "  ✓ API enabled"

# ── Step 2: Create the Docker repository (idempotent) ─────────
echo ""
echo "[2/5] Creating Artifact Registry repository '${REPO_NAME}' …"
if gcloud artifacts repositories describe "$REPO_NAME" \
        --location "$REGION" \
        --project  "$PROJECT" &>/dev/null; then
    echo "  ✓ Repository already exists, skipping"
else
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format docker \
        --location "$REGION" \
        --project  "$PROJECT" \
        --description "DELIVER pipeline container images"
    echo "  ✓ Repository created"
fi

# ── Step 3: Configure Docker to authenticate via gcloud ───────
echo ""
echo "[3/5] Configuring Docker auth for ${REGISTRY} …"
gcloud auth configure-docker "${REGISTRY}" --quiet
echo "  ✓ Docker auth configured"

# ── Step 4: Build the image ───────────────────────────────────
echo ""
echo "[4/5] Building Docker image …"
# Must be run from repo root so COPY src/... in Dockerfile works
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Step 4a: Stage the DELi source into the build context ─────
# The Dockerfile COPYs .deli-src/ (it no longer clones GitHub itself), so
# the image can be built from either the upstream repo or a local clone
# carrying unreleased fixes (e.g. the streaming-collect branch).
STAGE_DIR="${SCRIPT_DIR}/.deli-src"
rm -rf "$STAGE_DIR"
mkdir -p "$STAGE_DIR"
if [[ -n "${DELI_LOCAL_DIR:-}" ]]; then
    if ! git -C "$DELI_LOCAL_DIR" rev-parse --git-dir &>/dev/null; then
        echo "ERROR: --deli-dir '$DELI_LOCAL_DIR' is not a git repository" >&2
        exit 1
    fi
    DELI_BRANCH=$(git -C "$DELI_LOCAL_DIR" rev-parse --abbrev-ref HEAD)
    DELI_COMMIT=$(git -C "$DELI_LOCAL_DIR" rev-parse HEAD)
    echo "  → DELi source: local clone $DELI_LOCAL_DIR (branch ${DELI_BRANCH}, ${DELI_COMMIT:0:9})"
    if [[ -n "$(git -C "$DELI_LOCAL_DIR" status --porcelain)" ]]; then
        echo "  ⚠ local DELi clone has uncommitted changes — only the COMMITTED state is staged"
    fi
    # examples/, tests/, docs/ are not needed to install the package (one
    # example CSV alone is 54 MB)
    git -C "$DELI_LOCAL_DIR" archive --format=tar HEAD -- . \
        ':(exclude)examples' ':(exclude)tests' ':(exclude)docs' \
        | tar -x -C "$STAGE_DIR"
    echo "${DELI_COMMIT} (local ${DELI_BRANCH})" > "$STAGE_DIR/DELI_COMMIT"
else
    DELI_REF="${DELI_REF:-patch}"
    echo "  → DELi source: GitHub Popov-Lab-UNC/DELi @ ${DELI_REF}"
    git clone --branch "$DELI_REF" --depth 1 \
        https://github.com/Popov-Lab-UNC/DELi.git "$STAGE_DIR"
    echo "$(git -C "$STAGE_DIR" rev-parse HEAD) (github ${DELI_REF})" > "$STAGE_DIR/DELI_COMMIT"
    rm -rf "$STAGE_DIR/.git"
fi

# ORA support: forward ORAD_URL (from .env) so the image installs `orad` for
# .ora inputs. If ORAD_URL is unset/empty the image builds WITHOUT orad and any
# .ora decode (CONCAT, DECOMPRESS, ORA_DIAGNOSTICS) fails with 'orad: not found'.
if [[ -n "${ORAD_URL:-}" ]]; then
    echo "  → ORA support: installing orad from ORAD_URL"
else
    echo "  ⚠ ORAD_URL not set in .env — image will NOT support .ora inputs"
fi

LATEST_IMAGE="${REGISTRY}/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:latest"
BUILD_TAGS=( --tag "$FULL_IMAGE" )
if $PUSH_LATEST; then
    BUILD_TAGS+=( --tag "$LATEST_IMAGE" )
else
    echo "  → --no-latest: :latest will NOT be tagged or pushed"
fi

docker build \
    "${BUILD_TAGS[@]}" \
    --platform=linux/amd64 \
    --build-arg ORAD_URL="${ORAD_URL:-}" \
    --file Dockerfile \
    .

echo "  ✓ Image built: $FULL_IMAGE"

# ── Step 5: Push to Artifact Registry ────────────────────────
echo ""
echo "[5/5] Pushing image to Artifact Registry …"
docker push "$FULL_IMAGE"
if $PUSH_LATEST; then
    docker push "$LATEST_IMAGE"
fi
echo "  ✓ Image pushed"

# ── Grant Cloud Batch VMs pull access ────────────────────────
echo ""
echo "[info] Granting Compute SA pull access to the repository …"
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
# COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# gcloud artifacts repositories add-iam-policy-binding "$REPO_NAME" \
#     --location "$REGION" \
#     --project  "$PROJECT" \
#     --member   "serviceAccount:${COMPUTE_SA}" \
#     --role     "roles/artifactregistry.reader" \
#     --quiet
# echo "  ✓ Pull access granted to $COMPUTE_SA"

echo ""
echo "============================================================"
echo "  Done! Image available at:"
echo "  $FULL_IMAGE"
echo ""
echo "  Update nextflow.config container to:"
echo "  container = '${FULL_IMAGE}'"
echo "============================================================"