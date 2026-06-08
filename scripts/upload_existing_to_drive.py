"""
Script para fazer upload de arquivos existentes em /data/saved_comics para o Google Drive.
Execute via Console do Railway:
  python scripts/upload_existing_to_drive.py
"""
import os
import sys
import json

sys.path.insert(0, "/app")

from app.drive_upload import upload_image_to_drive, _get_drive_service
from googleapiclient.http import MediaFileUpload

SAVED_COMICS_DIR = "/data/saved_comics"

def upload_all():
    if not os.path.exists(SAVED_COMICS_DIR):
        print(f"Diretório {SAVED_COMICS_DIR} não encontrado.")
        return

    files = []
    for root, _, filenames in os.walk(SAVED_COMICS_DIR):
        for f in filenames:
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                files.append(os.path.join(root, f))

    if not files:
        print("Nenhuma imagem encontrada.")
        return

    print(f"Encontradas {len(files)} imagens. Iniciando upload...")

    for i, filepath in enumerate(files, 1):
        filename = os.path.basename(filepath)
        print(f"[{i}/{len(files)}] Enviando {filename}...", end=" ")
        file_id = upload_image_to_drive(filepath)
        if file_id:
            print(f"OK ({file_id})")
        else:
            print("ERRO")

    print("Concluído!")

if __name__ == "__main__":
    upload_all()
