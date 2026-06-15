"""Google Drive upload service for card sheet images.

Uploads finalized card sheet JPEGs to a shared Google Drive folder so
they can be synced to a PC via the Google Drive desktop app.

Configuration (environment variables):
    GOOGLE_APPLICATION_CREDENTIALS — path to service account JSON key.
    GDRIVE_FOLDER_ID              — target Google Drive folder ID.

The service account's email must have Editor access on the target folder.
"""

import logging
import os
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger("uvicorn.error")

_SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_credentials_path() -> str | None:
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if path:
        return path
    default = Path("credentials") / "gcp-vision-sa.json"
    return str(default) if default.exists() else None


def _get_folder_id() -> str | None:
    return os.environ.get("GDRIVE_FOLDER_ID")


def upload_to_drive(local_path: str, filename: str) -> str | None:
    """Upload a file to Google Drive.

    Args:
        local_path: Absolute path to the local file.
        filename: Desired filename on Google Drive.

    Returns:
        The Google Drive file ID on success, or None on failure.
    """
    folder_id = _get_folder_id()
    if not folder_id:
        logger.info("[GDRIVE] GDRIVE_FOLDER_ID not set, skipping upload")
        return None

    creds_path = _get_credentials_path()
    if not creds_path:
        logger.warning("[GDRIVE] No credentials found, skipping upload")
        return None

    try:
        creds = service_account.Credentials.from_service_account_file(
            creds_path, scopes=_SCOPES,
        )
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        media = MediaFileUpload(local_path, mimetype="image/jpeg", resumable=True)

        file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
        ).execute()

        file_id = file.get("id")
        logger.info("[GDRIVE] Uploaded %s → Drive file ID: %s", filename, file_id)
        return file_id

    except Exception as e:
        logger.warning("[GDRIVE] Upload failed (non-fatal): %s", e)
        return None
