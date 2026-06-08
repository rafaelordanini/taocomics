#!/bin/bash
set -e

# 1. Dependências do sistema
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv nodejs npm curl

# 2. Instala o Codex CLI
curl -fsSL https://chatgpt.com/codex/install.sh | sh

# 3. Cria e ativa virtualenv Python
python3 -m venv ~/codex-worker-env
source ~/codex-worker-env/bin/activate
pip install fastapi uvicorn

# 4. Copia o servidor
mkdir -p ~/codex-worker
cp server.py ~/codex-worker/server.py

echo ""
echo "=== Setup concluído ==="
echo "Próximo passo: autenticar o Codex com sua conta ChatGPT Plus:"
echo "  ~/.local/bin/codex login --device-auth"
echo ""
echo "Depois inicie o servidor:"
echo "  WORKER_TOKEN=sua_senha_secreta ~/codex-worker-env/bin/uvicorn codex-worker.server:app --host 0.0.0.0 --port 8080"
