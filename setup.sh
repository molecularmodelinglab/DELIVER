#!/bin/bash
# Run once on the login node to set up dependencies.
#
# Usage:
#   bash setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARAMS_FILE="${SCRIPT_DIR}/params.yml"

# ---------------------------------------------------------------------------
# 1. Python environment
# ---------------------------------------------------------------------------
module load python/3.12.4

# ---------------------------------------------------------------------------
# 2. Create DELIVER venv and install uv into it
# ---------------------------------------------------------------------------
echo "Creating venv at ${SCRIPT_DIR}/.venv..."
python -m venv "${SCRIPT_DIR}/.venv"
source "${SCRIPT_DIR}/.venv/bin/activate"

pip install uv
echo "uv version: $(uv --version)"

# ---------------------------------------------------------------------------
# 3. Install DELi into DELIVER venv
# ---------------------------------------------------------------------------
DELI_DIR=$(grep '^deli_dir:' "${PARAMS_FILE}" | awk '{print $2}' | tr -d '"')
echo "DELi directory: ${DELI_DIR}"

echo "Installing DELi..."
uv pip install "${DELI_DIR}"

# DELi uses pyarrow for parquet output but does not declare it as a dependency
echo "Installing missing DELi dependencies..."
uv pip install pyarrow

echo "Done: ${SCRIPT_DIR}/.venv"

# ---------------------------------------------------------------------------
# 4. DELi config initialization
# ---------------------------------------------------------------------------
if [ ! -f "$HOME/.deli/deli.config" ]; then
    echo "Initializing DELi config at ~/.deli..."
    deli config init
else
    echo "DELi config already exists at ~/.deli — skipping"
fi

echo ""
echo "Setup complete."
