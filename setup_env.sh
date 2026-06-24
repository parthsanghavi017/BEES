#!/usr/bin/env bash

# Exit immediately if a command exits with a non-zero status
set -e

ENV_NAME="clinical_pipeline"
PYTHON_VERSION="3.10"

echo "=== Initializing BEES Pipeline Environment Setup ==="

# Check if conda is installed
if ! command -v conda &> /dev/null; then
    echo "Error: conda could not be found. Please install Miniconda or Anaconda first."
    exit 1
fi

echo "Creating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
conda create -y -n "${ENV_NAME}" python="${PYTHON_VERSION}"

echo "Installing required Python dependencies..."
# Use conda run to execute pip install in the newly created environment without needing to activate it in the shell
conda run -n "${ENV_NAME}" pip install \
    fastapi \
    uvicorn \
    sqlalchemy \
    bcrypt \
    pandas \
    python-multipart \
    pyjwt \
    jinja2

echo "Creating local storage directory for VCF files..."
mkdir -p app/local_storage/vcfs
touch app/local_storage/vcfs/.gitkeep

echo "=== Environment Setup Completed successfully ==="
echo "To activate this environment, run: conda activate ${ENV_NAME}"
