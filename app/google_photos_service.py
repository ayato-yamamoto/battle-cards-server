"""
Google Photos API アップロードサービス

OAuth 2.0 を使用して Google Photos にカードシート画像をアップロードする。
- 初回実行時: ブラウザで認証 → リフレッシュトークンを token.json に保存
- 2回目以降: token.json からリフレッシュトークンを読み込み自動更新
"""

import json
import logging
import os
from pathlib import Path

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

# OAuth 2.0 スコープ（アップロード専用の最小権限）
SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]

# 認証ファイルパス（環境変数で上書き可能）
CREDENTIALS_PATH = os.getenv(
    "GOOGLE_PHOTOS_CREDENTIALS", "credentials/google_photos_credentials.json"
)
TOKEN_PATH = os.getenv("GOOGLE_PHOTOS_TOKEN", "credentials/google_photos_token.json")

# Google Photos API エンドポイント
UPLOAD_ENDPOINT = "https://photoslibrary.googleapis.com/v1/uploads"
CREATE_ENDPOINT = "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate"

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 認証
# ---------------------------------------------------------------------------


def authenticate() -> Credentials:
    """
    Google Photos API の認証を行い、有効な Credentials を返す。

    - token.json が存在すればリフレッシュトークンから自動更新
    - 存在しなければブラウザで OAuth 認証フローを実行し token.json に保存
    """
    creds: Credentials | None = None

    # 既存トークンの読み込み
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    # トークンが無効または期限切れの場合
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # リフレッシュトークンでアクセストークンを自動更新
            logger.info("[PHOTOS] アクセストークンをリフレッシュ中...")
            creds.refresh(Request())
        else:
            # 初回: ブラウザでOAuth認証フローを実行
            if not os.path.exists(CREDENTIALS_PATH):
                raise FileNotFoundError(
                    f"OAuth クライアント秘密情報が見つかりません: {CREDENTIALS_PATH}\n"
                    "Google Cloud Console からダウンロードして配置してください。"
                )
            logger.info("[PHOTOS] 初回認証: ブラウザでGoogleアカウントにログインしてください")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)

        # トークンを保存（次回以降の自動認証用）
        token_dir = os.path.dirname(TOKEN_PATH)
        if token_dir:
            os.makedirs(token_dir, exist_ok=True)
        with open(TOKEN_PATH, "w") as token_file:
            token_file.write(creds.to_json())
        logger.info("[PHOTOS] トークンを保存しました: %s", TOKEN_PATH)

    return creds


# ---------------------------------------------------------------------------
# ステップ 1: バイトデータのアップロード
# ---------------------------------------------------------------------------


def upload_bytes(creds: Credentials, image_path: str) -> str:
    """
    画像バイナリを Google Photos にアップロードし、uploadToken を取得する。

    Args:
        creds: 認証済みの Credentials
        image_path: アップロードする画像ファイルのパス

    Returns:
        uploadToken（文字列）

    Raises:
        RuntimeError: アップロード失敗時
    """
    # 画像のMIMEタイプを決定
    ext = Path(image_path).suffix.lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(ext, "image/jpeg")

    # リクエストヘッダー
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-type": "application/octet-stream",
        "X-Goog-Upload-Content-Type": mime_type,
        "X-Goog-Upload-Protocol": "raw",
    }

    # 画像バイナリを読み込んで送信
    with open(image_path, "rb") as f:
        image_data = f.read()

    logger.info(
        "[PHOTOS] ステップ1: バイナリアップロード中 (%d bytes, %s)",
        len(image_data),
        mime_type,
    )

    response = requests.post(UPLOAD_ENDPOINT, headers=headers, data=image_data)

    if response.status_code != 200:
        raise RuntimeError(
            f"[PHOTOS] バイナリアップロード失敗: "
            f"status={response.status_code}, body={response.text}"
        )

    upload_token = response.text
    if not upload_token:
        raise RuntimeError("[PHOTOS] アップロードトークンが空です")

    logger.info("[PHOTOS] ステップ1完了: uploadToken 取得成功")
    return upload_token


# ---------------------------------------------------------------------------
# ステップ 2: メディアアイテムの作成
# ---------------------------------------------------------------------------


def create_media_item(
    creds: Credentials, upload_token: str, description: str = ""
) -> dict:
    """
    uploadToken を使って Google Photos にメディアアイテムを作成する。

    Args:
        creds: 認証済みの Credentials
        upload_token: ステップ1で取得した uploadToken
        description: 画像の説明文（任意）

    Returns:
        API レスポンスの dict

    Raises:
        RuntimeError: メディアアイテム作成失敗時
    """
    headers = {
        "Authorization": f"Bearer {creds.token}",
        "Content-type": "application/json",
    }

    # リクエストボディ
    body = {
        "newMediaItems": [
            {
                "description": description,
                "simpleMediaItem": {"uploadToken": upload_token},
            }
        ]
    }

    logger.info("[PHOTOS] ステップ2: メディアアイテム作成中...")

    response = requests.post(CREATE_ENDPOINT, headers=headers, json=body)

    if response.status_code != 200:
        raise RuntimeError(
            f"[PHOTOS] メディアアイテム作成失敗: "
            f"status={response.status_code}, body={response.text}"
        )

    result = response.json()

    # レスポンスのステータス確認
    new_items = result.get("newMediaItemResults", [])
    if not new_items:
        raise RuntimeError("[PHOTOS] レスポンスに newMediaItemResults がありません")

    item_status = new_items[0].get("status", {})
    if item_status.get("message") != "Success":
        # "Success" 以外のケースもコード 0 (OK) で成功扱いの場合がある
        status_code = item_status.get("code", -1)
        if status_code != 0:
            raise RuntimeError(
                f"[PHOTOS] メディアアイテム作成エラー: {json.dumps(item_status)}"
            )

    logger.info("[PHOTOS] ステップ2完了: Google Photos へのアップロード成功")
    return result


# ---------------------------------------------------------------------------
# メイン関数: 画像をGoogle Photosにアップロード
# ---------------------------------------------------------------------------


def upload_to_google_photos(image_path: str, description: str = "") -> dict | None:
    """
    画像を Google Photos にアップロードする（2ステップ処理）。

    Args:
        image_path: アップロードする画像ファイルのパス
        description: 画像の説明文（任意）

    Returns:
        成功時は API レスポンスの dict、失敗時は None
    """
    if not os.path.exists(image_path):
        logger.error("[PHOTOS] ファイルが見つかりません: %s", image_path)
        return None

    try:
        # 認証（トークンの自動リフレッシュ含む）
        creds = authenticate()

        # ステップ1: バイナリアップロード → uploadToken 取得
        upload_token = upload_bytes(creds, image_path)

        # ステップ2: メディアアイテム作成
        result = create_media_item(creds, upload_token, description)

        return result

    except FileNotFoundError as e:
        logger.error("[PHOTOS] %s", e)
        return None
    except Exception as e:
        logger.error("[PHOTOS] アップロードエラー: %s", e)
        return None
