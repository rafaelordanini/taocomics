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
echo "=== [2/4] Rebuilding imagem do app ==="
$COMPOSE build app

echo ""
echo "=== [3/4] Subindo containers ==="
$COMPOSE up -d --force-recreate app

echo ""
echo "=== [4/4] Verificando... ==="
sleep 3
$COMPOSE ps
echo ""

# Confirma o fix da recursão
LINE=$(sudo docker exec taocomics-app-1 sed -n '106p' /app/app/agents.py 2>/dev/null || echo "(não verificado)")
echo "agents.py linha 106: $LINE"

echo ""
echo "✓ Atualização concluída."
