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

# 1. Hardware Acceleration Detection
echo "Checking for hardware acceleration capabilities..."
ACCEL_DETECTED=0

if command -v nvidia-smi &> /dev/null && nvidia-smi &> /dev/null; then
    echo "Hardware acceleration detected: NVIDIA GPU available via nvidia-smi."
    ACCEL_DETECTED=1
elif [[ "$(uname)" == "Darwin" ]] && (sysctl -n hw.optional.arm64 2>/dev/null | grep -q "1" || system_profiler SPHardwareDataType 2>/dev/null | grep -q "Apple M"); then
    echo "Hardware acceleration detected: Apple Silicon unified memory available."
    ACCEL_DETECTED=1
elif [[ "$(uname)" == "Linux" ]] && lspci 2>/dev/null | grep -qi "nvidia"; then
    echo "Hardware acceleration detected: NVIDIA GPU hardware detected in PCI devices."
    ACCEL_DETECTED=1
fi

if [ $ACCEL_DETECTED -eq 0 ]; then
    echo "WARNING: No hardware acceleration was detected. Ollama will fall back to CPU execution."
    echo "The LLM component will be slower, but it will still function correctly for report synthesis."
else
    echo "Hardware acceleration capability verified successfully."
fi

# 2. Ollama Daemon & Model Verification
echo "Verifying local Ollama service status..."
if ! command -v ollama &> /dev/null; then
    echo "Error: 'ollama' CLI could not be found. Please install Ollama from https://ollama.com/ first."
    exit 1
fi

# Check if Ollama daemon is responding on default port 11434
OLLAMA_HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --connect-timeout 3 http://127.0.0.1:11434/api/tags || true)

if [ "$OLLAMA_HTTP_STATUS" != "200" ]; then
    echo "Error: Ollama daemon is not running on port 11434."
    echo "To start the daemon, run 'ollama serve' in a separate terminal, then re-run this setup script."
    exit 1
else
    echo "Ollama daemon is running and actively listening on port 11434."
    
    # Check if medgemma:4b is present in the local manifest
    echo "Checking for 'medgemma:4b' model in Ollama tags..."
    if curl -s http://127.0.0.1:11434/api/tags | grep -q "medgemma:4b"; then
        echo "Model 'medgemma:4b' is present locally."
    else
        echo "Model 'medgemma:4b' not found. Pulling model 'medgemma:4b' from Ollama registry..."
        ollama pull medgemma:4b
        echo "Model 'medgemma:4b' pulled successfully."
    fi
fi

# 3. Environment Creation and Dependency Ingest
echo "Creating/updating conda environment '${ENV_NAME}' with Python ${PYTHON_VERSION}..."
# Use --yes and let it update if already exists
conda create -y -n "${ENV_NAME}" python="${PYTHON_VERSION}" || echo "Conda environment already exists, updating packages..."

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
    jinja2 \
    ollama \
    python-docx

echo "Creating local storage directory for VCF files..."
mkdir -p app/local_storage/vcfs
touch app/local_storage/vcfs/.gitkeep

echo "=== Environment Setup Completed successfully ==="
echo "To activate this environment, run: conda activate ${ENV_NAME}"

