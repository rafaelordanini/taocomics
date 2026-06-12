# Antigravity Worker (backup de texto via Gemini Pro)

Roda o **Antigravity CLI (`agy`)** no **host** (fora do Docker), usando sua
assinatura Gemini Pro/Antigravity como backup gratuito para os agentes de
texto do TaoComics — hoje ligado ao **Roteirista**.

Roda no host porque o `agy` guarda o login OAuth no keyring do sistema, que
não existe dentro de um container Docker.

## Instalação (uma vez)

```bash
bash antigravity-worker/setup.sh
~/.local/bin/agy auth login        # autentica com sua conta Gemini Pro
bash ~/antigravity-worker/restart.sh
```

Verifique:

```bash
curl http://localhost:8090/health
# {"status":"ok","agy_binary":true,"default_model":"gemini-3.1-pro"}
```

## Configuração no app

No `.env` do TaoComics:

```
ANTIGRAVITY_WORKER_URL=http://host.docker.internal:8090
WORKER_TOKEN=taocomics2024
```

Recrie o container do app para carregar a variável:

```bash
sudo docker-compose up -d --force-recreate app
```

## Como funciona

Quando a IA principal do Roteirista (Gemini API) falha, o pipeline tenta o
Antigravity (grátis, via assinatura) **antes** de cair para o OpenRouter (pago).
Se `ANTIGRAVITY_WORKER_URL` não estiver definido, o passo é simplesmente pulado.
