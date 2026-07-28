#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-$HOME/SubjectsDetection}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv-vision}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8010}"
CHASSIS_MODEL_PATH="${CHASSIS_MODEL_PATH:-$PROJECT_DIR/runs/chassis/yolo_chassis_parts/weights/best.pt}"

cd "$PROJECT_DIR"
source "$VENV_DIR/bin/activate"
export CHASSIS_MODEL_PATH
exec uvicorn vision_model.infer_service:app --host "$HOST" --port "$PORT"
