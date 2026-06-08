import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


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
    return build("drive", "v3", credentials=creds)


def upload_image_to_drive(filepath: str, folder_id: str = None) -> str | None:
    folder_id = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID não configurado — upload ignorado")
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_drive_service()
        filename = Path(filepath).name

        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaFileUpload(filepath, mimetype="image/png", resumable=False)
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        file_id = file.get("id")
        logger.info(f"[drive] Upload concluído: {filename} → {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"[drive] Erro ao fazer upload de {filepath}: {e}")
        return None
