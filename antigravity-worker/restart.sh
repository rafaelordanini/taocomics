#!/bin/bash
set -e

WORKER_DIR="$HOME/antigravity-worker"
VENV_UVICORN="$HOME/antigravity-worker-env/bin/uvicorn"
LOG_FILE="$WORKER_DIR/server.log"
WORKER_TOKEN="${WORKER_TOKEN:-taocomics2024}"
PORT="${ANTIGRAVITY_PORT:-8090}"
AGY_MODEL="${AGY_MODEL:-gemini-3.1-pro}"

echo "Parando servidor anterior..."
pkill -f "uvicorn server:app.*$PORT" 2>/dev/null || true
sleep 2

echo "Iniciando o Antigravity worker na porta $PORT..."
cd "$WORKER_DIR"
nohup env WORKER_TOKEN="$WORKER_TOKEN" AGY_MODEL="$AGY_MODEL" \
    "$VENV_UVICORN" server:app --host 0.0.0.0 --port "$PORT" > "$LOG_FILE" 2>&1 &

for i in 1 2 3 4 5 6; do
    sleep 3
    if curl -s "http://localhost:$PORT/health" | grep -q "ok"; then
        echo "✓ Antigravity worker rodando!"
        curl -s "http://localhost:$PORT/health"
        echo ""
        exit 0
    fi
done
echo "✗ Worker não respondeu. Veja o log:"
tail -20 "$LOG_FILE"
