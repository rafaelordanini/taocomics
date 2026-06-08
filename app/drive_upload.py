import os
import logging
import mimetypes
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_last_token_check: datetime | None = None

_folder_cache: dict[str, str] = {}


def _get_drive_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
    refresh_token = os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")

    if not all([client_id, client_secret, refresh_token]):
        raise RuntimeError("Variáveis GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET e GOOGLE_OAUTH_REFRESH_TOKEN não configuradas")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    creds.refresh(Request())

    # Verifica validade do token e alerta se próximo de expirar
    global _last_token_check
    now = datetime.now(timezone.utc)
    if _last_token_check is None or (now - _last_token_check).total_seconds() > 3600:
        _last_token_check = now
        if creds.expiry:
            remaining = (creds.expiry.replace(tzinfo=timezone.utc) - now).total_seconds()
            if remaining < 600:
                logger.warning(
                    f"[drive] ⚠️ Access token expira em {int(remaining)}s. "
                    "Se uploads pararem, renove o GOOGLE_OAUTH_REFRESH_TOKEN no Railway."
                )
        if not creds.valid:
            logger.warning(
                "[drive] ⚠️ Credenciais Google inválidas. "
                "Renove o GOOGLE_OAUTH_REFRESH_TOKEN no Railway via OAuth Playground."
            )

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
    """Faz upload de qualquer arquivo para o Google Drive preservando a estrutura de pastas relativa ao saved_comics."""
    root_folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not root_folder_id:
        return None
    if not all([os.environ.get("GOOGLE_OAUTH_CLIENT_ID"),
                os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET"),
                os.environ.get("GOOGLE_OAUTH_REFRESH_TOKEN")]):
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_drive_service()
        filepath = os.path.abspath(filepath)

        # Descobre o caminho relativo a partir de saved_comics
        saved_comics_dir = None
        for base in ("/data/saved_comics", "/app/app/static/saved_comics"):
            if filepath.startswith(base):
                saved_comics_dir = base
                break

        if saved_comics_dir:
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

        meta = {"name": filename, "parents": [parent_id]}
        media = MediaFileUpload(filepath, mimetype=mime, resumable=False)
        file = service.files().create(body=meta, media_body=media, fields="id").execute()
        file_id = file.get("id")
        logger.info(f"[drive] Upload: {filename} → {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"[drive] Erro ao fazer upload de {filepath}: {e}")
        return None


def upload_image_to_drive(filepath: str, folder_id: str = None) -> str | None:
    return upload_file_to_drive(filepath)
