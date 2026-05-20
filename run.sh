#!/bin/bash
# 家計簿アプリ起動スクリプト
# バックエンド(FastAPI) と フロントエンド(Streamlit) を同時に起動します

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
export PYTHONPATH="$ROOT"

# バックエンド起動
uvicorn backend.main:app --reload --port 8000 &
BACKEND_PID=$!

echo "バックエンド起動中 (PID=$BACKEND_PID, port=8000)..."
sleep 2

# フロントエンド起動
export KAKEIBO_API_URL="http://localhost:8000"
streamlit run "$ROOT/frontend/kakeibo_frontend.py" --server.port 8501

# Ctrl+C でバックエンドも停止
trap "kill $BACKEND_PID 2>/dev/null" EXIT
