#!/usr/bin/env bash
set -e

echo "========================================================"
echo "             Starting AI Reels Clipper"
echo "========================================================"

python -m app.doctor || echo "[WARNING] Doctor found potential issues. Continuing anyway..."

echo "Starting FastAPI server at http://127.0.0.1:8000 ..."
python -m uvicorn app.api.server:app --host 127.0.0.1 --port 8000 --reload
