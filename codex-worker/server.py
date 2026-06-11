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
    full_prompt = f"Generate an image: {prompt}. Image size 1024x1536."
    # --no-project-doc evita carregar histórico/contexto anterior que causaria compactação
    cmd = [
        CODEX_BIN,
        "--dangerously-bypass-approvals-and-sandbox",
        "--no-project-doc",
        "exec",
        full_prompt,
        "--skip-git-repo-check",
    ]

    env = os.environ.copy()
    # Garante sessão limpa sem histórico de conversa acumulado
    env.pop("CODEX_HOME", None)

    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
    except subprocess.TimeoutExpired:
        # Mata processos Codex zumbis para não travar a próxima geração
        subprocess.run(["pkill", "-f", "codex"], capture_output=True)
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": "Codex timeout após 5 minutos"}
        return
    except FileNotFoundError:
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Codex binary not found at {CODEX_BIN}"}
        return

    if result.returncode != 0:
        stderr_snippet = result.stderr[-500:] if result.stderr else ""
        stdout_snippet = result.stdout[-300:] if result.stdout else ""
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Codex falhou (rc={result.returncode}): {stderr_snippet} | stdout: {stdout_snippet}"}
        return

    # Detecta resposta de compactação de contexto (Codex imprime resumo em vez de gerar imagem)
    compaction_markers = ["context compaction", "compacting context", "summarizing conversation"]
    stdout_lower = result.stdout.lower()
    if any(m in stdout_lower for m in compaction_markers):
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": "Codex entrou em modo de compactação de contexto. Execute 'pkill -f codex' na VM e tente novamente."}
        return

    for _ in range(20):
        after = _list_images()
        new_imgs = [(fp, mt) for fp, mt in after if fp not in before]
        if new_imgs:
            break
        time.sleep(0.5)
    else:
        stdout_snippet = result.stdout[-300:] if result.stdout else "(vazio)"
        with jobs_lock:
            jobs[job_id] = {"status": "error", "error": f"Codex executou mas nenhuma imagem foi gerada. stdout: {stdout_snippet}"}
        return

    new_imgs.sort(key=lambda x: x[1], reverse=True)
    newest = new_imgs[0][0]

    with open(newest, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    with jobs_lock:
        jobs[job_id] = {"status": "done", "b64_json": b64}


class GenerateRequest(BaseModel):
    prompt: str
    image_b64: str = None  # imagem opcional em base64 (PNG) para análise


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

    prompt = req.prompt
    temp_img_path = None

    # Se imagem foi enviada, salva temporariamente e adiciona o caminho ao prompt
    if req.image_b64:
        tmp_dir = os.path.expanduser("~/.codex")
        os.makedirs(tmp_dir, exist_ok=True)
        temp_img_path = os.path.join(tmp_dir, f"temp_vision_{int(time.time())}.png")
        with open(temp_img_path, "wb") as f:
            f.write(base64.b64decode(req.image_b64))
        prompt += f"\n\nPlease analyze the image located at this file path: {temp_img_path}"

    cmd = [CODEX_BIN, "--dangerously-bypass-approvals-and-sandbox", "exec", prompt, "--skip-git-repo-check"]

    try:
        result = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Codex timeout")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail=f"Codex binary not found at {CODEX_BIN}")
    finally:
        if temp_img_path and os.path.exists(temp_img_path):
            try:
                os.remove(temp_img_path)
            except Exception:
                pass

    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=f"Codex falhou: {result.stderr[:500]}")

    return {"text": result.stdout.strip()}


@app.post("/restart")
def restart_worker(authorization: str = Header(default="")):
    if AUTH_TOKEN and authorization != f"Bearer {AUTH_TOKEN}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    # Mata processos Codex travados
    subprocess.run(["pkill", "-f", "codex"], capture_output=True)
    # Limpa jobs com erro/pendentes há mais de 10 min
    cutoff = time.time() - 600
    with jobs_lock:
        stale = [jid for jid, j in jobs.items() if j.get("status") in ("pending", "error")]
        for jid in stale:
            jobs.pop(jid, None)
    return {"status": "restarted", "cleared_jobs": len(stale)}


@app.get("/health")
def health():
    codex_ok = os.path.exists(CODEX_BIN)
    return {"status": "ok", "codex_binary": codex_ok}
