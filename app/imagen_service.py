"""Imagen 2 (Vertex AI) image generation service for battle cards.

Uses Google Cloud Vertex AI Imagen API to generate battle card images
from person photos. Requires a GCP service account with Vertex AI
permissions and the Imagen API enabled.

Configuration (environment variables):
    IMAGEN_CREDENTIALS — path to Imagen service account JSON key
                         (falls back to GOOGLE_APPLICATION_CREDENTIALS,
                          then credentials/imagen-sa.json).
    GCP_PROJECT_ID     — GCP project ID (auto-read from SA key if unset).
    GCP_LOCATION       — Vertex AI region (default: us-central1).
    IMAGEN_MODEL       — Imagen model for editing (default: imagen-3.0-capability-001).
"""

import json
import os
import time
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

# Model for image editing with subject/style references
IMAGEN_MODEL = os.getenv("IMAGEN_MODEL", "imagen-3.0-capability-001")

CARD_THEMES = [
    "(Fire) Theme: orange and red flames with burning embers and sparks",
    "(Thunder) Theme: blue-purple lightning bolts and electric energy",
    "(Nature) Theme: green magical aura with floating leaves",
    "(Void) Theme: purple and black mystical energy",
    "(Light) Theme: golden divine radiance and glow",
]


def _get_credentials_path() -> str | None:
    """Resolve Imagen service account key path.

    Priority: IMAGEN_CREDENTIALS env → GOOGLE_APPLICATION_CREDENTIALS env
              → credentials/imagen-sa.json default file.
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
    """Generate a single battle card using Imagen (Vertex AI).

    Same interface as gemini_service.generate_battle_card so it can be
    used as a drop-in replacement.

    Args:
        image_bytes: The source person photo bytes.
        mime_type: MIME type of the source photo.
        name: Player name (for logging).
        location: Location name (for logging).
        card_index: 1-based card index.
        total_cards: Total number of cards being generated.
        template_bytes: Optional template card image (PNG).

    Returns the generated image bytes (PNG) or None on failure.
    """
    print(f"[IMAGEN] 生成開始: カード {card_index} / {total_cards} - {name}")

    try:
        client = get_vertex_client()
    except RuntimeError as e:
        print(f"[IMAGEN] Vertex AI client init failed: {e}")
        return None

    theme = CARD_THEMES[(card_index - 1) % len(CARD_THEMES)]
    model = IMAGEN_MODEL

    # Build reference images
    reference_images: list[types.SubjectReferenceImage | types.StyleReferenceImage] = []

    # Person's photo as subject reference (preserve face/likeness)
    reference_images.append(
        types.SubjectReferenceImage(
            referenceImage=types.Image(imageBytes=image_bytes, mimeType=mime_type),
            referenceId=0,
            config=types.SubjectReferenceConfig(
                subjectType=types.SubjectReferenceType.SUBJECT_TYPE_PERSON,
            ),
        )
    )

    # Template as style reference (if available)
    if template_bytes:
        reference_images.append(
            types.StyleReferenceImage(
                referenceImage=types.Image(
                    imageBytes=template_bytes, mimeType="image/png"
                ),
                referenceId=1,
                config=types.StyleReferenceConfig(
                    styleDescription="fantasy battle card template with decorative frame",
                ),
            )
        )

    # Build prompt
    prompt = (
        f"Generate a fantasy battle card featuring the person from the reference photo.\n"
        f"{theme}\n\n"
        f"[Required Rules]\n"
        f"- The person must be the central figure on the card\n"
        f"- Change the outfit and hairstyle to match the battle card theme\n"
        f"- Keep the face photorealistic — do NOT convert to illustration or cartoon style\n"
        f"- Do NOT output any text, numbers, or characters on the card\n"
        f"- Use vivid and high-contrast colors suitable for inkjet printing\n"
        f"- Portrait orientation battle card\n"
    )

    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.edit_image(
                model=model,
                prompt=prompt,
                reference_images=reference_images,
                config=types.EditImageConfig(
                    numberOfImages=1,
                    safetyFilterLevel=types.SafetyFilterLevel.BLOCK_NONE,
                    personGeneration=types.PersonGeneration.ALLOW_ALL,
                    outputMimeType="image/png",
                ),
            )

            # Extract image from response
            if response.generated_images:
                gen_img = response.generated_images[0]

                # Check for safety filter
                if gen_img.rai_filtered_reason:
                    print(
                        f"[IMAGEN] カード {card_index}: RAI filtered: "
                        f"{gen_img.rai_filtered_reason} (attempt {attempt}/{max_attempts})"
                    )
                    if attempt < max_attempts:
                        time.sleep(2)
                        continue
                    return None

                img_data = gen_img.image
                if img_data and img_data.image_bytes:
                    print(
                        f"[IMAGEN] 生成成功: カード {card_index} / {total_cards} ({model})"
                    )
                    return img_data.image_bytes

            # No image in response
            print(
                f"[IMAGEN] カード {card_index}: レスポンスに画像なし "
                f"(attempt {attempt}/{max_attempts})"
            )
            if attempt < max_attempts:
                time.sleep(2)
                continue

        except Exception as e:
            print(
                f"[IMAGEN] カード {card_index}: エラー ({e}) "
                f"(attempt {attempt}/{max_attempts})"
            )
            if attempt < max_attempts:
                time.sleep(2)
                continue

    return None
