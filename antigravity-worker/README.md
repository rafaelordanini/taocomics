# Antigravity Worker (backup multimodal via Gemini Pro)

Roda o **Antigravity CLI (`agy`)** no **host** (fora do Docker), usando sua
assinatura Gemini Pro/Antigravity como backup gratuito **multimodal** para os
agentes de texto/visão do TaoComics: **Roteirista, Designer, Revisor e
Especialista China**. (O Artista NÃO usa Antigravity — só gpt-image-2.)

Imagens são enviadas como arquivo e o `agy` as analisa pelo caminho local,
então os agentes que dependem de imagem de referência também funcionam.

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

Na cadeia de fallback dos agentes de texto/visão, o Antigravity entra entre o
Codex e o Poe (ambos antes do OpenRouter pago):

```
Gemini Flash → Codex → Antigravity (Gemini Pro) → Gemini Pro nativo → Poe → OpenRouter
```

Se `ANTIGRAVITY_WORKER_URL` não estiver definido, o passo é simplesmente pulado.
