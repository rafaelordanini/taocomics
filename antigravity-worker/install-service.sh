#!/bin/bash
set -e

# Instala o Antigravity worker como serviço systemd de usuário, para subir
# automaticamente no boot. Roda como serviço de USUÁRIO (não root) porque o
# login do agy fica guardado no keyring da sua sessão.

SERVICE_SRC="$(dirname "$(readlink -f "$0")")/antigravity-worker.service"
DEST_DIR="$HOME/.config/systemd/user"

echo "=== Instalando o serviço systemd de usuário ==="
mkdir -p "$DEST_DIR"
cp "$SERVICE_SRC" "$DEST_DIR/antigravity-worker.service"

# Garante que o servidor está copiado para ~/antigravity-worker
mkdir -p "$HOME/antigravity-worker"
cp "$(dirname "$(readlink -f "$0")")/server.py" "$HOME/antigravity-worker/server.py"

echo "Parando qualquer worker iniciado manualmente (libera a porta 8090)..."
pkill -f "uvicorn server:app.*8090" 2>/dev/null || true
sleep 2

echo "Recarregando o systemd de usuário..."
systemctl --user daemon-reload
systemctl --user enable antigravity-worker.service
systemctl --user restart antigravity-worker.service

# Permite que o serviço continue rodando mesmo sem sessão aberta (boot sem login)
echo "Habilitando linger (mantém o serviço ativo no boot sem login)..."
sudo loginctl enable-linger "$USER" || echo "Aviso: não foi possível habilitar linger (precisa de sudo)."

sleep 3
echo ""
echo "=== Status ==="
systemctl --user --no-pager status antigravity-worker.service | head -12 || true
echo ""
echo "Teste:  curl -s http://localhost:8090/health"
echo "Logs:   journalctl --user -u antigravity-worker -f"
