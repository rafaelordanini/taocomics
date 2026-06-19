#!/usr/bin/env bash
# Atualiza o TaoComics com os últimos commits e reinicia os containers.
# Uso: bash atualizar.sh
set -e

BRANCH="claude/blissful-bohr-bbn596"
COMPOSE="sudo docker-compose"

echo "=== [1/4] Buscando commits do branch $BRANCH ==="
git fetch origin "$BRANCH"
git merge --no-edit "origin/$BRANCH"

echo ""
echo "=== [2/5] Rebuilding imagens ==="
$COMPOSE build app worker

echo ""
echo "=== [3/5] Subindo containers ==="
$COMPOSE up -d --force-recreate app worker

echo ""
echo "=== [4/5] Verificando... ==="
sleep 3
$COMPOSE ps
echo ""

echo "=== [5/5] Checagens ==="
# Confirma o fix da recursão
LINE=$(sudo docker exec taocomics-app-1 sed -n '106p' /app/app/agents.py 2>/dev/null || echo "(não verificado)")
echo "agents.py linha 106: $LINE"

# Confirma suporte a images_b64 no worker
LINE2=$(sudo docker exec taocomics-worker-1 grep -c "images_b64" /app/server.py 2>/dev/null || echo "(não verificado)")
echo "worker images_b64 refs: $LINE2"

echo ""
echo "✓ Atualização concluída."
