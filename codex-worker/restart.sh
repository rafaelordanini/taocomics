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
if [ -z "$GITHUB_TOKEN" ]; then
    echo "⚠️  GITHUB_TOKEN não definido. Pulando atualização do server.py (usando versão atual)."
else
    curl -fsSL -H "Authorization: token $GITHUB_TOKEN" \
        "https://raw.githubusercontent.com/rafaelordanini/taocomics/main/codex-worker/server.py" \
        -o "$WORKER_DIR/server.py"
    echo "✓ server.py atualizado."
fi

echo "Iniciando servidor..."
cd "$WORKER_DIR"
nohup env WORKER_TOKEN="$WORKER_TOKEN" "$VENV_UVICORN" server:app --host 0.0.0.0 --port 8080 > "$LOG_FILE" 2>&1 &

# Aguarda até 18 segundos pelo servidor subir (6 tentativas × 3s)
for i in 1 2 3 4 5 6; do
    sleep 3
    if curl -s http://localhost:8080/health | grep -q "ok"; then
        echo "✓ Servidor rodando com sucesso!"
        curl -s http://localhost:8080/health
        exit 0
    fi
done
echo "✗ Servidor não respondeu. Verifique o log:"
tail -20 "$LOG_FILE"
