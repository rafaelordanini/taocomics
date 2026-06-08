import os
import base64
import subprocess
import time
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

AUTH_TOKEN = os.environ.get("WORKER_TOKEN", "")
CODEX_BIN = os.path.expanduser("~/.local/bin/codex")
GEN_DIR = os.path.expanduser("~/.codex/generated_images")


def _list_images():
    imgs = []
    if os.path.isdir(GEN_DIR):
        for root, _, files in os.walk(GEN_DIR):
            for f in files:
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    fp = os.path.join(root, f)
                    try:
                        imgs.append((fp, os.path.getmtime(fp)))
                    except Exception:
                        pass
    return imgs


class GenerateRequest(BaseModel):
    prompt: str


@app.post("/generate-image")
def generate_image(req: GenerateRequest, authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    before = {fp for fp, _ in _list_images()}

    prompt_truncado = req.prompt[:800]
    full_prompt = f"Generate an image: {prompt_truncado}. Image size 1024x1536."

    cmd = [CODEX_BIN, "--dangerously-bypass-approvals-and-sandbox", "exec", full_prompt, "--skip-git-repo-check"]

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=3600
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Codex timeout")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Codex binary not found at {CODEX_BIN}")

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Codex falhou: {result.stderr[:500]}")

    # Aguarda até 10s para imagem aparecer no diretório
    for _ in range(20):
        after = _list_images()
        new_imgs = [(fp, mt) for fp, mt in after if fp not in before]
        if new_imgs:
            break
        time.sleep(0.5)
    else:
        raise HTTPException(status_code=500, detail="Codex executou mas nenhuma imagem foi gerada")

    new_imgs.sort(key=lambda x: x[1], reverse=True)
    newest = new_imgs[0][0]

    with open(newest, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    return {"b64_json": b64}


@app.post("/run-text")
def run_text(req: GenerateRequest, authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    cmd = [CODEX_BIN, "--dangerously-bypass-approvals-and-sandbox", "exec", req.prompt, "--skip-git-repo-check"]

    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Codex timeout")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Codex binary not found at {CODEX_BIN}")

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Codex falhou: {result.stderr[:500]}")

    return {"text": result.stdout.strip()}


@app.get("/health")
def health():
    codex_ok = os.path.exists(CODEX_BIN)
    return {"status": "ok", "codex_binary": codex_ok}
