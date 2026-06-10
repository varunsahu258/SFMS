"""Google Drive upload integration for SFMS backups."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import auth
from config import DB_PATH
from oauth_credentials import load_oauth_token
from utils import now_str

TOKEN_SETTING = "gdrive_token_json"
DRIVE_FOLDER = "SFMS_Backups"
DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _connect() -> sqlite3.Connection:
    """Open the configured live SFMS database."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def upload_to_drive(filepath) -> str | None:
    """Upload a backup into the SFMS_Backups Drive folder and log the result."""
    path = Path(filepath)
    with _connect() as conn:
        token_json = load_oauth_token()
        if not token_json:
            return None
        if not path.is_file():
            raise FileNotFoundError(str(path))
        credentials = Credentials.from_authorized_user_info(json.loads(token_json), DRIVE_SCOPES)
        service = build("drive", "v3", credentials=credentials, cache_discovery=False)
        query = f"name='{DRIVE_FOLDER}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        folders = service.files().list(q=query, spaces="drive", fields="files(id,name)", pageSize=1).execute().get("files", [])
        if folders:
            folder_id = folders[0]["id"]
        else:
            folder = service.files().create(
                body={"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"},
                fields="id",
            ).execute()
            folder_id = folder["id"]
        uploaded = service.files().create(
            body={"name": path.name, "parents": [folder_id]},
            media_body=MediaFileUpload(str(path), resumable=True),
            fields="id",
        ).execute()
        file_id = uploaded.get("id")
        conn.execute(
            "INSERT INTO backups_log (filename, created_at, created_by, type) VALUES (?, ?, ?, 'DRIVE')",
            (
                f"drive:{file_id}:{path.name}",
                now_str(),
                auth.CURRENT_SESSION.username if auth.CURRENT_SESSION is not None else "SYSTEM",
            ),
        )
        conn.commit()
        return file_id
