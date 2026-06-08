#!/bin/bash
set -e

WORKER_DIR="$HOME/codex-worker"
VENV_UVICORN="$HOME/codex-worker-env/bin/uvicorn"
LOG_FILE="$WORKER_DIR/server.log"
WORKER_TOKEN="${WORKER_TOKEN:-taocomics2024}"

echo "Parando servidor anterior..."
pkill -f "uvicorn server:app" 2>/dev/null || true
sleep 2

echo "Baixando server.py atualizado..."
curl -fsSL "https://raw.githubusercontent.com/rafaelordanini/taocomics/main/codex-worker/server.py" -o "$WORKER_DIR/server.py"

echo "Iniciando servidor..."
cd "$WORKER_DIR"
nohup env WORKER_TOKEN="$WORKER_TOKEN" "$VENV_UVICORN" server:app --host 0.0.0.0 --port 8080 > "$LOG_FILE" 2>&1 &

sleep 3
if curl -s http://localhost:8080/health | grep -q "ok"; then
    echo "✓ Servidor rodando com sucesso!"
    curl -s http://localhost:8080/health
else
    echo "✗ Servidor não respondeu. Verifique o log:"
    tail -20 "$LOG_FILE"
fi
