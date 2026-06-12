import os
import queue
import shutil
import secrets
import threading
import logging
from pydantic import BaseModel
from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from app.agents import processar_conto_taoista, find_tale_dir_by_filename, execute_page_edit

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Taoist Comic Generator")

# --------------------------------------------------------------------------
# Autenticação — login simples com cookie de sessão
# --------------------------------------------------------------------------
AUTH_USER = os.environ.get("APP_USERNAME", "rafaelordanini")
AUTH_PASS = os.environ.get("APP_PASSWORD", "Rafa1135m!")
_auth_tokens: set = set()
_auth_lock = threading.Lock()

# Rotas liberadas sem login (a página de login e o healthcheck)
_PUBLIC_PATHS = {"/login", "/api/login", "/health"}


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path in _PUBLIC_PATHS:
        return await call_next(request)

    token = request.cookies.get("tao_session", "")
    with _auth_lock:
        authorized = token in _auth_tokens

    if not authorized:
        # APIs recebem 401 JSON; navegação recebe redirect para /login
        if path.startswith("/api/") or path.startswith("/saved_comics/"):
            return JSONResponse({"error": "Não autenticado."}, status_code=401)
        return RedirectResponse(url="/login", status_code=302)

    return await call_next(request)


_LOGIN_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TaoComics — Login</title>
<style>
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:linear-gradient(160deg,#181525,#0f0d1a); font-family:'Segoe UI',sans-serif; }
  .card { background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1);
          border-radius:16px; padding:2.5rem; width:320px; text-align:center;
          box-shadow:0 8px 40px rgba(0,0,0,0.5); }
  h1 { color:#d4af37; font-size:1.5rem; margin:0 0 0.3rem; }
  p  { color:#aaa; font-size:0.85rem; margin:0 0 1.5rem; }
  input { width:100%; box-sizing:border-box; padding:0.7rem 0.9rem; margin-bottom:0.8rem;
          background:rgba(255,255,255,0.06); border:1px solid rgba(255,255,255,0.15);
          border-radius:8px; color:#eee; font-size:0.95rem; outline:none; }
  input:focus { border-color:#d4af37; }
  button { width:100%; padding:0.75rem; background:#d4af37; color:#181525; border:none;
           border-radius:8px; font-size:1rem; font-weight:700; cursor:pointer; }
  button:hover { filter:brightness(1.1); }
  .err { color:#ff6b6b; font-size:0.85rem; min-height:1.2em; margin-top:0.7rem; }
</style>
</head>
<body>
  <div class="card">
    <h1>☯ TaoComics</h1>
    <p>Gerador de HQs Taoístas</p>
    <form onsubmit="doLogin(event)">
      <input type="text" id="user" placeholder="Usuário" autocomplete="username" required>
      <input type="password" id="pass" placeholder="Senha" autocomplete="current-password" required>
      <button type="submit">Entrar</button>
      <div class="err" id="err"></div>
    </form>
  </div>
<script>
async function doLogin(e) {
  e.preventDefault();
  const res = await fetch("/api/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username: document.getElementById("user").value,
                          password: document.getElementById("pass").value})
  });
  const data = await res.json();
  if (data.status === "success") { window.location.href = "/"; }
  else { document.getElementById("err").textContent = data.error || "Falha no login."; }
}
</script>
</body>
</html>"""


@app.get("/login")
async def login_page():
    return HTMLResponse(_LOGIN_HTML)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/login")
async def do_login(req: LoginRequest):
    if secrets.compare_digest(req.username, AUTH_USER) and secrets.compare_digest(req.password, AUTH_PASS):
        token = secrets.token_urlsafe(32)
        with _auth_lock:
            _auth_tokens.add(token)
        resp = JSONResponse({"status": "success"})
        resp.set_cookie(
            "tao_session", token,
            httponly=True, samesite="lax",
            max_age=60 * 60 * 24 * 30,  # 30 dias
        )
        return resp
    return JSONResponse({"error": "Usuário ou senha incorretos."}, status_code=401)


@app.post("/api/logout")
async def do_logout(request: Request):
    token = request.cookies.get("tao_session", "")
    with _auth_lock:
        _auth_tokens.discard(token)
    resp = JSONResponse({"status": "success"})
    resp.delete_cookie("tao_session")
    return resp

# Configuração de CORS para permitir desenvolvimento local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cria os diretórios necessários
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# Usa /data (volume Railway) se disponível, senão cai para /tmp
def _resolve_saved_comics_dir() -> str:
    for base in ("/data", "/tmp"):
        try:
            path = os.path.join(base, "saved_comics")
            os.makedirs(path, exist_ok=True)
            test = os.path.join(path, ".write_test")
            with open(test, "w") as f:
                f.write("ok")
            os.remove(test)
            logging.info(f"[STORAGE] Usando diretório: {path}")
            return path
        except OSError:
            logging.warning(f"[STORAGE] {base} não gravável, tentando próximo...")
            continue
    raise RuntimeError("Nenhum diretório gravável encontrado para saved_comics")

os.makedirs(STATIC_DIR, exist_ok=True)

def get_saved_comics_dir() -> str:
    return _resolve_saved_comics_dir()

# Log no startup para mostrar qual storage está sendo usado
_startup_dir = _resolve_saved_comics_dir()
logging.info(f"[STARTUP] Storage ativo: {_startup_dir} | /data existe: {os.path.exists('/data')} | /data gravável: {os.access('/data', os.W_OK)}")


class SessionState:
    def __init__(self):
        self.resume_event = threading.Event()
        self.pause_event = threading.Event()
        self.user_decision = None
        self.user_directive = None
        self.paused = False
        self.cancelled = False

active_sessions = {}

class DecisionRequest(BaseModel):
    session_id: str
    action: str
    directive: str = None

@app.post("/api/decision")
async def receive_decision(req: DecisionRequest):
    session_id = req.session_id
    action = req.action
    directive = req.directive
    if session_id in active_sessions:
        state = active_sessions[session_id]
        state.user_decision = action
        state.user_directive = directive
        state.resume_event.set()
        return {"status": "success"}
    return {"error": "Sessão não encontrada"}


class ControlRequest(BaseModel):
    session_id: str

@app.post("/api/pause")
async def pause_generation(req: ControlRequest):
    session_id = req.session_id
    if session_id in active_sessions:
        state = active_sessions[session_id]
        state.paused = True
        state.pause_event.clear()
        return {"status": "paused"}
    return {"error": "Sessão não encontrada"}

class ResumeRequest(BaseModel):
    session_id: str
    instrucoes: dict = None

@app.post("/api/resume")
async def resume_generation(req: ResumeRequest):
    session_id = req.session_id
    if session_id in active_sessions:
        state = active_sessions[session_id]
        if req.instrucoes:
            state.updated_instructions = req.instrucoes
        state.paused = False
        state.pause_event.set()
        return {"status": "resumed"}
    return {"error": "Sessão não encontrada"}

@app.post("/api/cancel")
async def cancel_generation(req: ControlRequest):
    session_id = req.session_id
    if session_id in active_sessions:
        state = active_sessions[session_id]
        state.cancelled = True
        state.pause_event.set()
        state.resume_event.set()
        return {"status": "cancelled"}
    return {"error": "Sessão não encontrada"}

class BalanceRequest(BaseModel):
    gemini_api_key: str = None
    openai_api_key: str = None
    openrouter_api_key: str = None
    poe_api_key: str = None

@app.post("/api/balances")
async def get_llm_balances(req: BalanceRequest):
    import httpx
    # 1. Codex
    codex_data = {
        "status": "success",
        "value": "Gratuito / Ilimitado",
        "percentage": 100,
        "color": "#4caf50"
    }

    # 2. Poe
    poe_key = req.poe_api_key or os.getenv("POE_API_KEY")
    poe_data = {"status": "error", "value": "Chave não configurada", "percentage": 0, "color": "#777"}
    if poe_key:
        try:
            headers = {"Authorization": f"Bearer {poe_key}"}
            async with httpx.AsyncClient() as client:
                r = await client.get("https://api.poe.com/usage/current_balance", headers=headers, timeout=10.0)
            if r.status_code == 200:
                data = r.json()
                pts = data.get("current_point_balance", 0)
                usd = data.get("total_balance_usd", "0.00")
                pts_formatted = f"{pts:,}".replace(",", ".")
                poe_data = {
                    "status": "success",
                    "value": f"{pts_formatted} pts (${usd})",
                    "percentage": min(100, int((pts / 1000000) * 100)) if pts else 0,
                    "color": "#9c27b0"
                }
            else:
                poe_data = {"status": "error", "value": f"Erro {r.status_code}", "percentage": 0, "color": "#e74c3c"}
        except Exception as e:
            poe_data = {"status": "error", "value": "Erro de conexão", "percentage": 0, "color": "#e74c3c"}

    # 3. OpenRouter
    or_key = req.openrouter_api_key or os.getenv("OPENROUTER_API_KEY")
    or_data = {"status": "error", "value": "Chave não configurada", "percentage": 0, "color": "#777"}
    if or_key:
        try:
            headers = {"Authorization": f"Bearer {or_key}"}
            async with httpx.AsyncClient() as client:
                r = await client.get("https://openrouter.ai/api/v1/credits", headers=headers, timeout=10.0)
            if r.status_code == 200:
                res_json = r.json()
                if "data" in res_json:
                    data = res_json["data"]
                    total = data.get("total_credits", 0.0)
                    usage = data.get("total_usage", 0.0)
                    remaining = total - usage
                    remaining_formatted = f"${remaining:.2f}"
                    percentage = int((remaining / total) * 100) if total > 0 else 0
                    or_data = {
                        "status": "success",
                        "value": remaining_formatted,
                        "percentage": max(0, min(100, percentage)),
                        "color": "#ff9800"
                    }
                else:
                    or_data = {"status": "error", "value": "Resposta inválida", "percentage": 0, "color": "#e74c3c"}
            else:
                or_data = {"status": "error", "value": f"Erro {r.status_code}", "percentage": 0, "color": "#e74c3c"}
        except Exception as e:
            or_data = {"status": "error", "value": "Erro de conexão", "percentage": 0, "color": "#e74c3c"}

    # 4. OpenAI
    openai_data = {
        "status": "info",
        "value": "Consultar Dashboard",
        "url": "https://platform.openai.com/usage",
        "color": "#2196f3"
    }

    # 5. Gemini
    gemini_data = {
        "status": "info",
        "value": "Consultar Google Cloud",
        "url": "https://console.cloud.google.com/billing",
        "color": "#2196f3"
    }

    return {
        "codex": codex_data,
        "poe": poe_data,
        "openrouter": or_data,
        "openai": openai_data,
        "gemini": gemini_data
    }


def _create_generation_session(conto: str, body: dict):
    """Cria uma sessão de geração (estado + mensagens) e devolve (session_id, run_fn).
    run_fn executa o pipeline de forma SÍNCRONA — quem chama decide a thread."""
    instrucoes = body.get("instrucoes")
    if not isinstance(instrucoes, dict):
        instrucoes = {}
    artista_model = body.get("artista_model", "codex/gpt-image-2")
    ref_image_b64 = body.get("ref_image")

    custom_keys = {
        "gemini_api_key": body.get("gemini_api_key"),
        "openai_api_key": body.get("openai_api_key"),
        "openrouter_api_key": body.get("openrouter_api_key"),
        "poe_api_key": body.get("poe_api_key"),
        "anthropic_api_key": body.get("anthropic_api_key") or body.get("claude_api_key"),
    }

    ref_image = None
    if ref_image_b64:
        import base64
        import io
        from PIL import Image
        try:
            image_bytes = base64.b64decode(ref_image_b64)
            ref_image = Image.open(io.BytesIO(image_bytes))
        except Exception:
            pass

    import uuid
    session_id = str(uuid.uuid4())
    state = SessionState()
    state.pause_event.set()
    active_sessions[session_id] = state

    state.messages = []
    state.messages_lock = threading.Lock()
    state.done = False

    def sse_send(message: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%d/%m %H:%M:%S")
        stamped = f"[{timestamp}] {message}"
        with state.messages_lock:
            state.messages.append(stamped)

    def check_status():
        if state.cancelled:
            raise RuntimeError("Geração cancelada pelo usuário.")
        if state.paused:
            sse_send("[Sistema] Geração pausada pelo usuário. Aguardando comando para continuar...")
            state.pause_event.clear()
            state.pause_event.wait()
            if state.cancelled:
                raise RuntimeError("Geração cancelada pelo usuário.")
            if hasattr(state, "updated_instructions") and state.updated_instructions:
                sse_send("[Sistema] Mesclando novas diretivas e instruções enviadas pelo usuário...")
                for agent, values in state.updated_instructions.items():
                    if agent not in instrucoes:
                        instrucoes[agent] = {}
                    if isinstance(values, dict):
                        for k, v in values.items():
                            instrucoes[agent][k] = v
                state.updated_instructions = None
            sse_send("[Sistema] Geração retomada!")

    def wait_for_user_decision(page_num: int, filename: str = None) -> tuple[str, str]:
        if state.cancelled:
            raise RuntimeError("Geração cancelada pelo usuário.")
        temp_img = filename.replace(".png", "_temp.png") if filename else ""
        sse_send(f"[PAUSA] {page_num}|{temp_img}")
        state.resume_event.clear()
        state.resume_event.wait()
        if state.cancelled:
            raise RuntimeError("Geração cancelada pelo usuário.")
        return state.user_decision, state.user_directive

    def run_pipeline():
        try:
            processar_conto_taoista(
                conto=conto,
                output_dir=get_saved_comics_dir(),
                sse_send=sse_send,
                custom_keys=custom_keys,
                ref_image=ref_image,
                instrucoes=instrucoes,
                wait_for_user_decision=wait_for_user_decision,
                check_status=check_status,
                artista_model=artista_model
            )
            sse_send("[FIM]")
        except Exception as e:
            sse_send(f"[ERRO] {str(e)}")
            sse_send("[FIM]")
        finally:
            state.done = True
            def _cleanup():
                import time
                time.sleep(300)
                active_sessions.pop(session_id, None)
            threading.Thread(target=_cleanup, daemon=True).start()

    return session_id, run_pipeline


@app.post("/api/generate")
async def generate_comic(request: Request):
    body = await request.json()
    conto = body.get("conto")
    if not conto:
        return {"error": "O texto do conto é obrigatório."}

    session_id, run_pipeline = _create_generation_session(conto, body)
    threading.Thread(target=run_pipeline, daemon=True).start()
    return {"session_id": session_id}


# --------------------------------------------------------------------------
# Fila de processamento em lote — vários contos (.txt), um por vez.
# Se o conto não tiver título, o Roteirista inventa (regra já existente).
# --------------------------------------------------------------------------
batch_queue: list = []          # itens: {id, nome, status, session_id, erro}
_batch_lock = threading.Lock()
_batch_worker_running = False


def _batch_worker():
    """Processa a fila sequencialmente — um conto por vez."""
    global _batch_worker_running
    while True:
        item = None
        with _batch_lock:
            for it in batch_queue:
                if it["status"] == "aguardando":
                    item = it
                    break
            if item is None:
                _batch_worker_running = False
                return
            item["status"] = "processando"

        try:
            session_id, run_pipeline = _create_generation_session(item["conto"], item["config"])
            with _batch_lock:
                item["session_id"] = session_id
            run_pipeline()  # SÍNCRONO — segura a fila até o conto terminar
            state = active_sessions.get(session_id)
            erro = None
            if state:
                with state.messages_lock:
                    for m in reversed(state.messages):
                        if "[ERRO]" in m:
                            erro = m
                            break
            with _batch_lock:
                item["status"] = "erro" if erro else "concluido"
                item["erro"] = erro
        except Exception as e:
            with _batch_lock:
                item["status"] = "erro"
                item["erro"] = str(e)


@app.post("/api/generate-batch")
async def generate_batch(request: Request):
    """Recebe vários contos e os enfileira. Formato:
    {"contos": [{"nome": "arquivo.txt", "texto": "..."}], ...demais configs do /api/generate}"""
    global _batch_worker_running
    body = await request.json()
    contos = body.get("contos") or []
    if not contos:
        return {"error": "Nenhum conto enviado."}

    config = {k: v for k, v in body.items() if k != "contos"}

    import uuid
    added = []
    with _batch_lock:
        for c in contos:
            texto = (c.get("texto") or "").strip()
            if not texto:
                continue
            item = {
                "id": str(uuid.uuid4()),
                "nome": c.get("nome") or f"conto_{len(batch_queue) + 1}",
                "conto": texto,
                "config": config,
                "status": "aguardando",
                "session_id": None,
                "erro": None,
            }
            batch_queue.append(item)
            added.append(item["id"])

        if added and not _batch_worker_running:
            _batch_worker_running = True
            threading.Thread(target=_batch_worker, daemon=True).start()

    return {"status": "success", "enfileirados": len(added), "ids": added}


@app.get("/api/queue")
async def get_queue():
    """Status da fila de processamento em lote."""
    with _batch_lock:
        items = [
            {k: v for k, v in it.items() if k not in ("conto", "config")}
            for it in batch_queue
        ]
    return {"queue": items}


@app.delete("/api/queue/{item_id}")
async def remove_queue_item(item_id: str):
    """Remove um item da fila (só se ainda não começou ou já terminou)."""
    with _batch_lock:
        for it in batch_queue:
            if it["id"] == item_id:
                if it["status"] == "processando":
                    return {"error": "Item em processamento — cancele pela sessão ativa."}
                batch_queue.remove(it)
                return {"status": "success"}
    return {"error": "Item não encontrado."}


@app.get("/api/session/{session_id}/messages")
async def get_session_messages(session_id: str, since: int = 0):
    if session_id not in active_sessions:
        # Sessão pode ter terminado e sido removida — retorna done
        return {"messages": [], "done": True}
    state = active_sessions[session_id]
    with state.messages_lock:
        msgs = state.messages[since:]
        done = state.done
    # Remove a sessão da memória somente depois que o cliente confirmar que recebeu tudo
    if done and session_id in active_sessions:
        # Deixa o cliente coletar a última leva; limpa após o done ser entregue
        # A limpeza definitiva ocorre 60 s depois para tolerar reconexões
        pass
    return {"messages": msgs, "done": done}


# Diagnóstico de storage
@app.get("/api/storage-info")
async def storage_info():
    data_exists = os.path.exists("/data")
    data_writable = os.access("/data", os.W_OK) if data_exists else False
    active_dir = get_saved_comics_dir()
    try:
        files = os.listdir(active_dir)
    except Exception as e:
        files = [f"ERRO: {e}"]
    return {
        "active_dir": active_dir,
        "using_persistent_volume": active_dir.startswith("/data"),
        "/data_exists": data_exists,
        "/data_writable": data_writable,
        "files_count": len(files),
        "files": files[:20],
    }


# Rota para expor uma API simples para listar as HQs salvas
@app.get("/api/comics")
async def list_comics():
    try:
        files = os.listdir(get_saved_comics_dir())
        images = [f for f in files if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        images.sort()
        return {"images": images}
    except Exception as e:
        return {"error": str(e)}


@app.delete("/api/comics/{filename}")
async def delete_comic(filename: str):
    """Apaga uma imagem gerada do diretório saved_comics."""
    # Bloqueia path traversal — só aceita o nome do arquivo, sem barras
    if "/" in filename or "\\" in filename or ".." in filename:
        return {"error": "Nome de arquivo inválido."}
    filepath = os.path.join(get_saved_comics_dir(), filename)
    if not os.path.exists(filepath):
        return {"error": "Arquivo não encontrado."}
    try:
        os.remove(filepath)
        return {"status": "success", "deleted": filename}
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------
# Explorador de arquivos — navega toda a árvore de saved_comics (pastas,
# pareceres de texto, imagens) e permite excluir do servidor pelo navegador.
# --------------------------------------------------------------------------

def _safe_resolve(rel_path: str) -> str | None:
    """Resolve um caminho relativo dentro de saved_comics, bloqueando path traversal.
    Retorna o caminho absoluto ou None se for inválido/fora da base."""
    base = os.path.abspath(get_saved_comics_dir())
    rel_path = (rel_path or "").strip().lstrip("/")
    target = os.path.abspath(os.path.join(base, rel_path))
    # Garante que o alvo está estritamente dentro da base (ou é a própria base)
    if target != base and not target.startswith(base + os.sep):
        return None
    return target


@app.get("/api/browse")
async def browse_files(path: str = Query("")):
    """Lista pastas e arquivos em um nível da árvore de saved_comics."""
    target = _safe_resolve(path)
    if target is None or not os.path.isdir(target):
        return {"error": "Caminho inválido ou inexistente.", "entries": []}

    base = os.path.abspath(get_saved_comics_dir())
    rel = os.path.relpath(target, base)
    rel = "" if rel == "." else rel
    parent = "" if rel == "" else os.path.dirname(rel)

    image_exts = (".png", ".jpg", ".jpeg", ".webp", ".gif")
    text_exts = (".txt", ".json", ".md")
    entries = []
    try:
        for name in sorted(os.listdir(target)):
            if name.startswith("."):
                continue
            full = os.path.join(target, name)
            entry_rel = os.path.join(rel, name) if rel else name
            if os.path.isdir(full):
                try:
                    count = len([n for n in os.listdir(full) if not n.startswith(".")])
                except Exception:
                    count = 0
                entries.append({"name": name, "path": entry_rel, "type": "dir", "count": count})
            else:
                lower = name.lower()
                size = os.path.getsize(full)
                entries.append({
                    "name": name,
                    "path": entry_rel,
                    "type": "file",
                    "is_image": lower.endswith(image_exts),
                    "is_text": lower.endswith(text_exts),
                    "size": size,
                })
    except Exception as e:
        return {"error": str(e), "entries": []}

    # Pastas primeiro, depois arquivos
    entries.sort(key=lambda e: (e["type"] != "dir", e["name"].lower()))
    return {"path": rel, "parent": parent, "entries": entries}


@app.get("/api/browse-file")
async def browse_file_content(path: str = Query("")):
    """Retorna o conteúdo de um arquivo de texto (parecer, roteiro, prompt)."""
    target = _safe_resolve(path)
    if target is None or not os.path.isfile(target):
        return {"error": "Arquivo inválido ou inexistente."}
    if os.path.getsize(target) > 2 * 1024 * 1024:
        return {"error": "Arquivo muito grande para visualização."}
    try:
        with open(target, "r", encoding="utf-8", errors="replace") as f:
            return {"content": f.read()}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/browse-raw")
async def browse_file_raw(path: str = Query("")):
    """Serve um arquivo binário (imagem) de qualquer nível da árvore."""
    target = _safe_resolve(path)
    if target is None or not os.path.isfile(target):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(target)


@app.delete("/api/browse")
async def delete_browse_entry(path: str = Query("")):
    """Exclui um arquivo OU uma pasta (recursivo) dentro de saved_comics."""
    target = _safe_resolve(path)
    base = os.path.abspath(get_saved_comics_dir())
    if target is None or target == base:
        return {"error": "Caminho inválido."}
    if not os.path.exists(target):
        return {"error": "Caminho não encontrado."}
    try:
        if os.path.isdir(target):
            shutil.rmtree(target)
        else:
            os.remove(target)
        return {"status": "success", "deleted": path}
    except Exception as e:
        return {"error": str(e)}


class EditPageRequest(BaseModel):
    filename: str
    instruction: str
    gemini_api_key: str = None
    openai_api_key: str = None
    openrouter_api_key: str = None
    poe_api_key: str = None
    anthropic_api_key: str = None
    claude_api_key: str = None
    artista_model: str = None
    ref_image_b64: str = None


@app.get("/api/page-prompt/{filename}")
async def get_page_prompt(filename: str):
    tale_dir = find_tale_dir_by_filename(filename)
    if not tale_dir:
        return {"prompt": None, "error": "Diretório do conto não encontrado."}
    parts = filename.split("_pagina_")
    if len(parts) < 2:
        return {"prompt": None, "error": "Formato de arquivo inválido."}
    try:
        page_num = int(parts[1].replace(".png", ""))
    except ValueError:
        return {"prompt": None, "error": "Número de página inválido."}
    prompt_path = os.path.join(tale_dir, f"prompt_pagina_{page_num}.txt")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return {"prompt": f.read().strip()}
    return {"prompt": None}


@app.post("/api/edit-page")
async def edit_page(req: EditPageRequest):
    filename = req.filename
    instruction = req.instruction

    tale_dir = find_tale_dir_by_filename(filename)
    if not tale_dir:
        return {"error": "Diretório do conto não encontrado para a página especificada."}

    keys = {
        "gemini_api_key": req.gemini_api_key,
        "openai_api_key": req.openai_api_key,
        "openrouter_api_key": req.openrouter_api_key,
        "poe_api_key": req.poe_api_key,
        "anthropic_api_key": req.anthropic_api_key or req.claude_api_key,
    }

    try:
        success = execute_page_edit(
            tale_dir, filename, instruction, keys,
            req.artista_model or "codex/gpt-image-2",
            ref_image_b64=req.ref_image_b64
        )
        if success:
            return {"status": "success"}
        else:
            return {"error": "Falha ao editar a página."}
    except Exception as e:
        return {"error": str(e)}


@app.post("/api/drive-sync-all")
async def drive_sync_all():
    """Envia todos os arquivos de saved_comics para o Google Drive."""
    from app.drive_upload import upload_file_to_drive, get_drive_status

    # Diagnóstico de conexão antes de tentar enviar
    ok, msg = get_drive_status()
    if not ok:
        return {"uploaded": 0, "failed": 0, "files": [], "errors": [msg]}

    base_dir = get_saved_comics_dir()
    uploaded = []
    failed = []

    for root, dirs, files in os.walk(base_dir):
        dirs[:] = [d for d in dirs if d != "rejeitadas"]
        for fname in files:
            if fname.startswith("."):
                continue
            fpath = os.path.join(root, fname)
            try:
                file_id = upload_file_to_drive(fpath)
                if file_id:
                    uploaded.append(os.path.relpath(fpath, base_dir))
                else:
                    failed.append(os.path.relpath(fpath, base_dir))
            except Exception as e:
                failed.append(f"{os.path.relpath(fpath, base_dir)} ({e})")

    return {"uploaded": len(uploaded), "failed": len(failed), "files": uploaded, "errors": failed}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/saved_comics/{filename}")
async def serve_saved_comic(filename: str):
    filepath = os.path.join(get_saved_comics_dir(), filename)
    if not os.path.exists(filepath):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(filepath)


# Servir os arquivos estáticos (HTML, CSS, JS e imagens geradas)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
