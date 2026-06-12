import os
import time
import base64
import subprocess
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

AUTH_TOKEN = os.environ.get("WORKER_TOKEN", "")

# Binário do Antigravity CLI (instalado pelo instalador oficial em ~/.local/bin/agy)
AGY_BIN = (
    os.path.expanduser("~/.local/bin/agy")
    if os.path.exists(os.path.expanduser("~/.local/bin/agy"))
    else "/usr/local/bin/agy"
)

# Modelo padrão (Gemini Pro via assinatura Antigravity). Pode ser sobrescrito por request.
DEFAULT_MODEL = os.environ.get("AGY_MODEL", "gemini-3.1-pro")


class TextRequest(BaseModel):
    prompt: str
    system: str = None
    model: str = None
    image_b64: str = None  # imagem opcional (PNG base64) para análise multimodal


def _run_agy(prompt: str, model: str = None) -> str:
    cmd = [AGY_BIN, "-p", prompt]
    if model:
        cmd += ["--model", model]
    elif DEFAULT_MODEL:
        cmd += ["--model", DEFAULT_MODEL]

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        subprocess.run(["pkill", "-f", "agy"], capture_output=True)
        raise HTTPException(status_code=504, detail="Antigravity (agy) timeout após 5 minutos")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Binário agy não encontrado em {AGY_BIN}")

    if result.returncode != 0:
        stderr = (result.stderr or "")[-500:]
        raise HTTPException(status_code=500, detail=f"Antigravity falhou (rc={result.returncode}): {stderr}")

    return result.stdout.strip()


@app.post("/run-text")
def run_text(req: TextRequest, authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    prompt = req.prompt
    if req.system:
        # agy não tem flag de system prompt dedicada no modo -p; prefixamos no prompt
        prompt = f"{req.system}\n\n---\n\n{prompt}"

    temp_img_path = None
    if req.image_b64:
        tmp_dir = os.path.expanduser("~/.antigravity-worker-tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        temp_img_path = os.path.join(tmp_dir, f"vision_{int(time.time()*1000)}.png")
        with open(temp_img_path, "wb") as f:
            f.write(base64.b64decode(req.image_b64))
        prompt += f"\n\nAnalise a imagem localizada neste caminho de arquivo: {temp_img_path}"

    try:
        text = _run_agy(prompt, model=req.model)
    finally:
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception:
                pass

    return {"text": text}


@app.get("/health")
def health():
    agy_ok = os.path.exists(AGY_BIN)
    return {"status": "ok", "agy_binary": agy_ok, "default_model": DEFAULT_MODEL}
