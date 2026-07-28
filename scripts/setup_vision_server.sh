#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/SubjectsDetection}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv-vision}"
TORCH_INDEX_URL="${TORCH_INDEX_URL:-https://download.pytorch.org/whl/cu128}"

echo "[1/6] System"
hostname
uname -a

echo "[2/6] NVIDIA"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi
else
  echo "nvidia-smi not found. Install NVIDIA driver before training."
fi

echo "[3/6] Python venv"
cd "$PROJECT_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip wheel setuptools

echo "[4/6] PyTorch CUDA wheels"
python -m pip install torch torchvision --index-url "$TORCH_INDEX_URL"

echo "[5/6] Vision dependencies"
python -m pip install -r vision_model/requirements.txt

echo "[6/6] Verification"
python - <<'PY'
import torch
print("torch", torch.__version__)
print("cuda_available", torch.cuda.is_available())
if torch.cuda.is_available():
    print("gpu", torch.cuda.get_device_name(0))
PY

echo "Vision environment ready: $VENV_DIR"
