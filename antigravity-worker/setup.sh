#!/bin/bash
set -e

# Setup do Antigravity worker — roda no HOST (fora do Docker), onde o login
# OAuth do agy persiste no keyring do sistema.

echo "=== 1. Instalando o Antigravity CLI (agy) ==="
if ! command -v ~/.local/bin/agy >/dev/null 2>&1 && ! command -v agy >/dev/null 2>&1; then
    curl -fsSL https://antigravity.google/install.sh | sh
else
    echo "agy já instalado."
fi

echo ""
echo "=== 2. Criando virtualenv Python ==="
python3 -m venv ~/antigravity-worker-env
~/antigravity-worker-env/bin/pip install --quiet fastapi uvicorn

echo ""
echo "=== 3. Copiando o servidor ==="
mkdir -p ~/antigravity-worker
cp "$(dirname "$0")/server.py" ~/antigravity-worker/server.py

echo ""
echo "=== Setup concluído ==="
echo "Próximo passo — autenticar com sua conta Gemini Pro/Antigravity:"
echo "  ~/.local/bin/agy auth login"
echo ""
echo "Depois inicie o worker:"
echo "  bash ~/antigravity-worker/restart.sh"
