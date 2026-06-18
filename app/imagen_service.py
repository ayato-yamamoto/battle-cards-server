"""Vertex AI image generation service for battle cards.

Uses Gemini model via Vertex AI (service account auth) to generate
battle card images from person photos.

Configuration (environment variables):
    IMAGEN_CREDENTIALS — path to service account JSON key
                         (falls back to GOOGLE_APPLICATION_CREDENTIALS,
                          then credentials/imagen-sa.json).
    GCP_PROJECT_ID     — GCP project ID (auto-read from SA key if unset).
    GCP_LOCATION       — Vertex AI region (default: us-central1).
    VERTEX_MODEL       — model name (default: gemini-3.1-flash-image).
"""

import base64
import json
import os
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from dotenv import load_dotenv

load_dotenv()

VERTEX_MODEL = os.getenv("VERTEX_MODEL", "gemini-3.1-flash-image")

CARD_THEMES = [
    "(Fire) Theme: orange and red flames with burning embers and sparks",
    "(Thunder) Theme: blue-purple lightning bolts and electric energy",
    "(Nature) Theme: green magical aura with floating leaves",
    "(Void) Theme: purple and black mystical energy",
    "(Light) Theme: golden divine radiance and glow",
]

# Safety settings: relax all categories to BLOCK_NONE
_SAFETY_SETTINGS = [
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
    types.SafetySetting(
        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
        threshold=types.HarmBlockThreshold.BLOCK_NONE,
    ),
]

_SAFETY_FINISH_REASONS = {
    "SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT",
    "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST",
}


def _get_credentials_path() -> str | None:
    """Resolve service account key path.

    Priority: IMAGEN_CREDENTIALS env -> GOOGLE_APPLICATION_CREDENTIALS env
              -> credentials/imagen-sa.json default file.
    """
    for env_var in ("IMAGEN_CREDENTIALS", "GOOGLE_APPLICATION_CREDENTIALS"):
        path = os.environ.get(env_var)
        if path and os.path.exists(path):
            return path
    default = Path("credentials") / "imagen-sa.json"
    return str(default) if default.exists() else None


def _get_gcp_project_id() -> str | None:
    project_id = os.environ.get("GCP_PROJECT_ID")
    if project_id:
        return project_id
    creds_path = _get_credentials_path()
    if creds_path and os.path.exists(creds_path):
        with open(creds_path) as f:
            return json.load(f).get("project_id")
    return None


def get_vertex_client() -> genai.Client:
    """Create a genai Client configured for Vertex AI."""
    project_id = _get_gcp_project_id()
    if not project_id:
        raise RuntimeError(
            "GCP_PROJECT_ID is not set and cannot be read from service account credentials"
        )
    location = os.environ.get("GCP_LOCATION", "us-central1")

    creds_path = _get_credentials_path()
    credentials = None
    if creds_path:
        from google.oauth2 import service_account as sa
        credentials = sa.Credentials.from_service_account_file(
            creds_path,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

    return genai.Client(
        vertexai=True,
        project=project_id,
        location=location,
        credentials=credentials,
    )


def generate_battle_card_imagen(
    image_bytes: bytes,
    mime_type: str,
    name: str,
    location: str,
    card_index: int,
    total_cards: int,
    template_bytes: Optional[bytes] = None,
) -> Optional[bytes]:
    """Generate a single battle card using Gemini via Vertex AI.

    Same interface as gemini_service.generate_battle_card so it can be
    used as a drop-in replacement.
    """
    print(f"[VERTEX] 生成開始: カード {card_index} / {total_cards} - {name}")

    try:
        client = get_vertex_client()
    except RuntimeError as e:
        print(f"[VERTEX] Vertex AI client init failed: {e}")
        return None

    theme = CARD_THEMES[(card_index - 1) % len(CARD_THEMES)]
    model = VERTEX_MODEL

    # Build content parts (same structure as gemini_service)
    parts: list[types.Part] = []

    if template_bytes:
        parts.append(types.Part.from_bytes(data=template_bytes, mime_type="image/png"))

    parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    if template_bytes:
        prompt = (
            f"The first image is a battle card template (background frame).\n"
            f"The second image is a person's photo.\n\n"
            f"While preserving the template card's frame, decorations, and background design, "
            f"arrange the person's photo in a battle card style.\n\n"
            f"{theme}\n\n"
            f"[Required Rules]\n"
            f"- Keep the template card frame, decorations, background design, and the name input space at the bottom\n"
            f"- Change the outfit and hairstyle to match the battle card theme\n"
            f"- Keep the face photorealistic — do NOT convert to illustration or cartoon style. Process based on the original photo\n"
            f"- Do NOT change the face or body angle, scale, or proportions when editing\n"
            f"- Do NOT output any text, Japanese or English characters\n"
            f"- Adjust brightness for optimal inkjet printing — avoid crushed or overly dark colors, use vivid and high-contrast neon colors\n\n"
            f"[Output Specifications]\n"
            f"- Print size: 63mm x 88mm\n"
            f"- Resolution: 600 DPI\n"
            f"- Required pixels: 1488 x 2079 pixels\n"
            f"- Do NOT include status display or name display\n"
        )
    else:
        prompt = (
            f"Transform the attached person's photo into a battle card style.\n"
            f"{theme}\n\n"
            f"[Required Rules]\n"
            f"- Change the outfit and hairstyle to match the battle card theme\n"
            f"- Keep the face photorealistic — do NOT convert to illustration or cartoon style. Process based on the original photo\n"
            f"- Keep consistent face and body size. Keep consistent top/bottom/left/right balance when positioning the person\n"
            f"- Keep consistent card layout, size, and atmosphere across all cards\n"
            f"- Do NOT output any text, Japanese or English characters\n"
            f"- Adjust brightness for optimal inkjet printing — avoid crushed or overly dark colors, use vivid and high-contrast neon colors\n\n"
            f"[Output Specifications]\n"
            f"- Print size: 63mm x 88mm\n"
            f"- Resolution: 600 DPI\n"
            f"- Required pixels: 1488 x 2079 pixels\n"
            f"- Do NOT include status display or name display\n"
        )

    parts.append(types.Part.from_text(text=prompt))

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[types.Content(role="user", parts=parts)],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    safety_settings=_SAFETY_SETTINGS,
                ),
            )

            # Log finish reason
            finish_reason = None
            if response.candidates:
                candidate = response.candidates[0]
                finish_reason = getattr(candidate, "finish_reason", None)
                safety_ratings = getattr(candidate, "safety_ratings", None)
                if finish_reason:
                    print(f"[VERTEX] カード {card_index}: finish_reason={finish_reason} ({model})")
                if safety_ratings:
                    print(f"[VERTEX] カード {card_index}: safety_ratings={safety_ratings} ({model})")

            # Check for safety block
            if finish_reason and finish_reason in _SAFETY_FINISH_REASONS:
                print(
                    f"[VERTEX] カード {card_index}: SAFETY BLOCKED ({finish_reason}) "
                    f"(attempt {attempt}/{max_attempts})"
                )
                if attempt < max_attempts:
                    time.sleep(2)
                    continue
                return None

            # Extract image from response
            candidate_content = response.candidates[0].content if response.candidates else None
            if candidate_content and candidate_content.parts:
                for part in candidate_content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        print(f"[VERTEX] 生成成功: カード {card_index} / {total_cards} ({model})")
                        data = part.inline_data.data
                        return base64.b64decode(data) if isinstance(data, str) else data

            if attempt < max_attempts:
                print(
                    f"[VERTEX] カード {card_index}: レスポンスに画像なし "
                    f"(finish_reason={finish_reason}), リトライ (attempt {attempt}/{max_attempts})"
                )
                time.sleep(2)
                continue
            print(
                f"[VERTEX] カード {card_index}: レスポンスに画像なし "
                f"(finish_reason={finish_reason}), 最終失敗"
            )

        except ServerError as e:
            if attempt < max_attempts:
                print(f"[VERTEX] カード {card_index}: ServerError ({e}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"[VERTEX] カード {card_index}: ServerError 最終失敗: {e}")

        except TypeError as e:
            if attempt < max_attempts:
                print(f"[VERTEX] カード {card_index}: TypeError ({e}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"[VERTEX] カード {card_index}: TypeError 最終失敗: {e}")

        except Exception as e:
            if attempt < max_attempts:
                print(f"[VERTEX] カード {card_index}: エラー ({e}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"[VERTEX] カード {card_index}: エラー 最終失敗: {e}")

    return None
