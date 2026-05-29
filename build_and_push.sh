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
#    --project, --region, --tag
#
#  Usage:
#    chmod +x build_and_push.sh
#    ./build_and_push.sh                                      # uses .env
#    ./build_and_push.sh --tag 1.0.0                          # override tag
#    ./build_and_push.sh --project my-proj --region us-east1  # override more
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
while [[ $# -gt 0 ]]; do
    case "$1" in
        --project) PROJECT="$2"; shift 2 ;;
        --region)  REGION="$2";  shift 2 ;;
        --tag)     TAG="$2";     shift 2 ;;
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

docker build \
    --tag  "$FULL_IMAGE" \
    --tag  "${REGISTRY}/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:latest" \
    --platform=linux/amd64 \
    --file Dockerfile \
    .

echo "  ✓ Image built: $FULL_IMAGE"

# ── Step 5: Push to Artifact Registry ────────────────────────
echo ""
echo "[5/5] Pushing image to Artifact Registry …"
docker push "$FULL_IMAGE"
docker push "${REGISTRY}/${PROJECT}/${REPO_NAME}/${IMAGE_NAME}:latest"
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