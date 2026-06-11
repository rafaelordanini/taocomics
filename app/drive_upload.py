import os
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_last_token_check: datetime | None = None

_folder_cache: dict[str, str] = {}


def _get_drive_service():
    from googleapiclient.discovery import build

    # Prefere OAuth (grava na cota pessoal de 15GB do usuário).
    # Service Account NÃO funciona com Drive pessoal gratuito: não tem cota
    # própria e exige Shared Drive (Google Workspace pago).
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")
    has_oauth = all([client_id, client_secret, refresh_token])

    if not has_oauth:
        # Sem OAuth completo: tenta Service Account apenas se houver
        sa_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_json:
            import json
            from google.oauth2.service_account import Credentials as SACredentials
            info = json.loads(sa_json)
            creds = SACredentials.from_service_account_info(
                info, scopes=["https://www.googleapis.com/auth/drive"]
            )
            return build("drive", "v3", credentials=creds)
        raise RuntimeError(
            "Configure as três variáveis OAuth (GOOGLE_OAUTH_CLIENT_ID, "
            "GOOGLE_OAUTH_CLIENT_SECRET, GOOGLE_OAUTH_REFRESH_TOKEN). "
            "Service Account não funciona com Google Drive pessoal gratuito."
        )

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())

    global _last_token_check
    now = datetime.now(timezone.utc)
    if _last_token_check is None or (now - _last_token_check).total_seconds() > 3600:
        _last_token_check = now
        if creds.expiry:
            remaining = (creds.expiry.replace(tzinfo=timezone.utc) - now).total_seconds()
            if remaining < 600:
                logger.warning(
                    f"[drive] ⚠️ Access token expira em {int(remaining)}s. "
                    "Renove o GOOGLE_OAUTH_REFRESH_TOKEN."
                )
        if not creds.valid:
            logger.warning("[drive] ⚠️ Credenciais Google inválidas.")

    return build("drive", "v3", credentials=creds)


def _get_or_create_folder(service, name: str, parent_id: str) -> str:
    cache_key = f"{parent_id}/{name}"
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    # Busca pasta existente
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed=false"
    res = service.files().list(q=q, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        folder = service.files().create(body=meta, fields="id").execute()
        folder_id = folder["id"]

    _folder_cache[cache_key] = folder_id
    return folder_id


def _ensure_drive_path(service, rel_path: str, root_folder_id: str) -> str:
    """Garante que a estrutura de pastas existe no Drive e retorna o ID da pasta final."""
    parts = Path(rel_path).parts
    current_id = root_folder_id
    for part in parts:
        current_id = _get_or_create_folder(service, part, current_id)
    return current_id


def upload_file_to_drive(filepath: str) -> str | None:
    """Faz upload (ou atualização) de qualquer arquivo para o Google Drive preservando a estrutura de pastas."""
    root_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not root_folder_id:
        logger.warning("[drive] GOOGLE_DRIVE_FOLDER_ID não configurado — upload ignorado.")
        return None
    has_sa = bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
    has_oauth = all(os.environ.get(v) for v in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"))
    if not has_sa and not has_oauth:
        logger.warning("[drive] Credenciais não configuradas (GOOGLE_SERVICE_ACCOUNT_JSON ou OAuth) — upload ignorado.")
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_drive_service()
        filepath = os.path.abspath(filepath)

        # Descobre o caminho relativo a partir de saved_comics — base-agnóstico
        # (funciona com /data/saved_comics, /tmp/saved_comics, /app/app/static/saved_comics, etc.)
        parts = filepath.split(os.sep)
        if "saved_comics" in parts:
            idx = len(parts) - 1 - parts[::-1].index("saved_comics")
            saved_comics_dir = os.sep.join(parts[: idx + 1])
            rel = os.path.relpath(os.path.dirname(filepath), saved_comics_dir)
            if rel == ".":
                parent_id = root_folder_id
            else:
                parent_id = _ensure_drive_path(service, rel, root_folder_id)
        else:
            parent_id = root_folder_id

        filename = os.path.basename(filepath)
        mime, _ = mimetypes.guess_type(filepath)
        mime = mime or "application/octet-stream"

        # Verifica se já existe arquivo com o mesmo nome na pasta — faz update em vez de criar duplicata
        q = f"name='{filename}' and '{parent_id}' in parents and trashed=false"
        existing = service.files().list(q=q, fields="files(id)").execute().get("files", [])

        media = MediaFileUpload(filepath, mimetype=mime, resumable=False)
        if existing:
            file_id = existing[0]["id"]
            service.files().update(fileId=file_id, media_body=media).execute()
            logger.info(f"[drive] Atualizado: {filename} → {file_id}")
        else:
            meta = {"name": filename, "parents": [parent_id]}
            file = service.files().create(body=meta, media_body=media, fields="id").execute()
            file_id = file.get("id")
            logger.info(f"[drive] Upload: {filename} → {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"[drive] Erro ao fazer upload de {filepath}: {e}")
        return None


def upload_image_to_drive(filepath: str, folder_id: str = None) -> str | None:
    return upload_file_to_drive(filepath)


def get_drive_status() -> tuple[bool, str]:
    """Diagnostica a conexão com o Google Drive. Retorna (ok, mensagem)."""
    if not os.environ.get("GOOGLE_DRIVE_FOLDER_ID"):
        return False, "Variável GOOGLE_DRIVE_FOLDER_ID não configurada."
    has_sa = bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"))
    has_oauth = all(os.environ.get(v) for v in ("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"))
    if not has_sa and not has_oauth:
        return False, "Configure GOOGLE_SERVICE_ACCOUNT_JSON ou as três variáveis OAuth."
    try:
        service = _get_drive_service()
        root_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
        # Valida que a pasta raiz existe e é acessível com o token atual
        service.files().get(fileId=root_folder_id, fields="id,name").execute()
        return True, "Conexão com o Google Drive OK."
    except Exception as e:
        msg = str(e)
        if "invalid_grant" in msg or "expired" in msg or "revoked" in msg:
            return False, ("Refresh token expirado ou revogado. Gere um novo "
                           "GOOGLE_OAUTH_REFRESH_TOKEN no OAuth Playground e atualize no Railway.")
        if "404" in msg or "notFound" in msg:
            return False, "GOOGLE_DRIVE_FOLDER_ID não encontrado ou sem permissão de acesso."
        return False, f"Falha na conexão com o Drive: {msg}"
