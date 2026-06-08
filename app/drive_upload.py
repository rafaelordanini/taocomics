import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_drive_service():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not creds_json:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON não configurado")

    info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def upload_image_to_drive(filepath: str, folder_id: str = None) -> str | None:
    """Faz upload de uma imagem para o Google Drive. Retorna o ID do arquivo ou None em caso de erro."""
    folder_id = folder_id or os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID não configurado — upload ignorado")
        return None

    try:
        from googleapiclient.http import MediaFileUpload

        service = _get_drive_service()
        filename = Path(filepath).name

        file_metadata = {"name": filename}
        if folder_id:
            file_metadata["parents"] = [folder_id]

        media = MediaFileUpload(filepath, mimetype="image/png", resumable=False)
        file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
        file_id = file.get("id")
        logger.info(f"[drive] Upload concluído: {filename} → {file_id}")
        return file_id
    except Exception as e:
        logger.error(f"[drive] Erro ao fazer upload de {filepath}: {e}")
        return None
