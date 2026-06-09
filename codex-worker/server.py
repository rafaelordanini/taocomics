import os
import base64
import subprocess
import time
import uuid
import threading
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI()

AUTH_TOKEN = os.environ.get("WORKER_TOKEN", "")
CODEX_BIN = os.path.expanduser("~/.local/bin/codex")
GEN_DIR = os.path.expanduser("~/.codex/generated_images")

# job_id -> {"status": "pending"|"done"|"error", "b64_json": str, "error": str}
jobs: dict = {}
jobs_lock = threading.Lock()


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


def _run_image_job(job_id: str, prompt: str):
    before = {fp for fp, _ in _list_images()}
    prompt_truncado = prompt[:4000]
    full_prompt = f"Generate an image: {prompt_truncado}. Image size 1024x1536."
    cmd = [CODEX_BIN, "--dangerously-bypass-approvals-and-sandbox", "exec", full_prompt, "--skip-git-repo-check"]

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300
        )
    except subprocess.TimeoutExpired:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": "Codex timeout após 5 minutos"}
        return
    except FileNotFoundError:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Codex binary not found at {CODEX_BIN}"}
        return

    if result.returncode != 0:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Codex falhou: {result.stderr[:500]}"}
        return

    for _ in range(20):
        after = _list_images()
        new_imgs = [(fp, mt) for fp, mt in after if fp not in before]
        if new_imgs:
            break
        time.sleep(0.5)
    else:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": "Codex executou mas nenhuma imagem foi gerada"}
        return

    new_imgs.sort(key=lambda x: x[1], reverse=True)
    newest = new_imgs[0][0]

    with open(newest, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    with jobs_lock:
        jobs[job_id] = {"status": "done", "b64_json": b64}


class GenerateRequest(BaseModel):
    prompt: str


@app.post("/generate-image")
def generate_image(req: GenerateRequest, authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    job_id = str(uuid.uuid4())
    with jobs_lock:
        jobs[job_id] = {"status": "pending"}

    thread = threading.Thread(target=_run_image_job, args=(job_id, req.prompt), daemon=True)
    thread.start()

    return {"job_id": job_id}


@app.get("/job/{job_id}")
def get_job(job_id: str, authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    with jobs_lock:
        job = jobs.get(job_id)

    if job is None:
        raise HTTPException(status_code=404, detail="Job não encontrado")

    return job


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
