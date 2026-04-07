#!/bin/bash
# Local Mac setup script — alternative to setup.sh for running outside Longleaf HPC.
#
# Differences from setup.sh:
#   - Uses uv to create venv with Python 3.13 (no "module load" needed on Mac)
#   - Requires uv to be installed: https://docs.astral.sh/uv/getting-started/installation/
#
# Run once from the DELIVER directory:
#   bash setup_local.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMS_FILE="${SCRIPT_DIR}/params_local.yml"

# ---------------------------------------------------------------------------
# 1. Verify uv is available
# ---------------------------------------------------------------------------
if ! command -v uv &>/dev/null; then
    echo "Error: uv not found. Install it with:"
    echo "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi
echo "uv version: $(uv --version)"

# ---------------------------------------------------------------------------
# 2. Create venv with Python 3.13
# ---------------------------------------------------------------------------
echo "Creating venv at ${SCRIPT_DIR}/.venv (Python 3.13)..."
uv venv --python 3.13 "${SCRIPT_DIR}/.venv"
source "${SCRIPT_DIR}/.venv/bin/activate"

# ---------------------------------------------------------------------------
# 3. Install DELi into the venv
# ---------------------------------------------------------------------------
DELI_DIR=$(grep '^deli_dir:' "${PARAMS_FILE}" | awk '{print $2}' | tr -d '"')
echo "DELi directory: ${DELI_DIR}"

echo "Installing DELi..."
uv pip install "${DELI_DIR}"

# DELi uses pyarrow for parquet output but does not declare it as a dependency
echo "Installing missing DELi dependencies..."
uv pip install pyarrow

# ---------------------------------------------------------------------------
# 4. DELi config initialization
# ---------------------------------------------------------------------------
# ~/.deli may exist as a file (old DELi config format) or as a directory
# (new format: ~/.deli/deli.config). Either is accepted by DELi.
# "deli config init" only runs if neither form exists yet.
if [ -e "$HOME/.deli" ]; then
    echo "DELi config already exists at ~/.deli — skipping init"
else
    echo "Initializing DELi config at ~/.deli..."
    deli config init
fi

echo ""
echo "Setup complete. Run the pipeline with:"
echo "  bash run_local.sh"
