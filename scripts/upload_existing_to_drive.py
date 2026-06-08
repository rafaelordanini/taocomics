"""
Script para fazer upload de TODOS os arquivos de /app/app/static/saved_comics para o Google Drive,
preservando a estrutura de pastas.
Execute via Console do Railway com as variáveis de ambiente.
"""
import os
import sys
import mimetypes

sys.path.insert(0, "/app")

SAVED_COMICS_DIR = "/app/app/static/saved_comics"
ROOT_FOLDER_ID = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")


def get_drive_service():
    from app.drive_upload import _get_drive_service
    return _get_drive_service()


def create_folder(service, name, parent_id):
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id]
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def upload_file(service, filepath, parent_id):
    filename = os.path.basename(filepath)
    mime, _ = mimetypes.guess_type(filepath)
    mime = mime or "application/octet-stream"
    from googleapiclient.http import MediaFileUpload
    meta = {"name": filename, "parents": [parent_id]}
    media = MediaFileUpload(filepath, mimetype=mime, resumable=False)
    file = service.files().create(body=meta, media_body=media, fields="id").execute()
    return file["id"]


def upload_tree(service, local_dir, drive_parent_id):
    for entry in sorted(os.listdir(local_dir)):
        entry_path = os.path.join(local_dir, entry)
        if os.path.isdir(entry_path):
            print(f"  📁 Criando pasta: {entry}")
            folder_id = create_folder(service, entry, drive_parent_id)
            upload_tree(service, entry_path, folder_id)
        else:
            print(f"  📄 Enviando: {entry}", end=" ")
            try:
                fid = upload_file(service, entry_path, drive_parent_id)
                print(f"✓ ({fid})")
            except Exception as e:
                print(f"✗ Erro: {e}")


def main():
    if not ROOT_FOLDER_ID:
        print("GOOGLE_DRIVE_FOLDER_ID não configurado.")
        return

    if not os.path.exists(SAVED_COMICS_DIR):
        print(f"Diretório {SAVED_COMICS_DIR} não encontrado.")
        return

    print(f"Conectando ao Google Drive...")
    service = get_drive_service()
    print(f"Iniciando upload de {SAVED_COMICS_DIR}...\n")
    upload_tree(service, SAVED_COMICS_DIR, ROOT_FOLDER_ID)
    print("\nConcluído!")


if __name__ == "__main__":
    main()
