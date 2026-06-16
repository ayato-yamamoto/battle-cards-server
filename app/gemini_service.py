
import base64
import os
import time
from typing import Optional

from google import genai
from google.genai import types
from google.genai.errors import ServerError
from dotenv import load_dotenv

load_dotenv() 

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
PRIMARY_MODEL = "gemini-3-pro-image"       # Nano Banana Pro 最新
FALLBACK_MODEL = "gemini-3.1-flash-image"  # Nano Banana 2 最新
# "gemini-3.1-flash-image-preview" → 20260717に廃止
# "gemini-3-pro-image-preview"

# Card themes for 5 battle cards (English for stability with safety filters)
CARD_THEMES = [
    "(Fire) Theme: orange and red flames with burning embers and sparks",
    "(Thunder) Theme: blue-purple lightning bolts and electric energy",
    "(Nature) Theme: green magical aura with floating leaves",
    "(Void) Theme: purple and black mystical energy",
    "(Light) Theme: golden divine radiance and glow",
]

# Safety settings: relax all categories to BLOCK_NONE for development
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

# Finish reasons that indicate safety block — retryable with sanitised prompt
_SAFETY_FINISH_REASONS = {
    "SAFETY", "IMAGE_SAFETY", "PROHIBITED_CONTENT",
    "IMAGE_PROHIBITED_CONTENT", "BLOCKLIST",
}


def get_client() -> genai.Client:
    api_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    return genai.Client(api_key=api_key)


def generate_battle_card(
    image_bytes: bytes,
    mime_type: str,
    name: str,
    location: str,
    card_index: int,
    total_cards: int,
    template_bytes: Optional[bytes] = None,
) -> Optional[bytes]:
    """Generate a single battle card image from a source photo using Gemini API.

    Args:
        image_bytes: The source photo bytes.
        mime_type: MIME type of the source photo.
        name: Player name (unused in prompt, kept for logging).
        location: Location name (unused in prompt, kept for logging).
        card_index: 1-based card index.
        total_cards: Total number of cards being generated.
        template_bytes: Optional template card image (PNG) to use as background reference.

    Returns the generated image bytes (PNG) or None if generation failed.
    """
    print(f"生成開始: カード {card_index} / {total_cards} - {name} at {location}")
    client = get_client()

    theme = CARD_THEMES[(card_index - 1) % len(CARD_THEMES)]

    # Build content parts
    parts: list[types.Part] = []

    # Add template image if provided
    if template_bytes:
        parts.append(types.Part.from_bytes(data=template_bytes, mime_type="image/png"))

    # Add source photo
    parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

    # Build prompt text in English for better stability with safety filters
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

    # Try primary model; on 503 (high demand), fall back to secondary
    try:
        result = _call_model_with_retry(
            client, PRIMARY_MODEL, parts, card_index, total_cards, name, location,
            raise_on_503=True,
        )
        if result is not None:
            return result
        return None  # non-503 failure — no fallback
    except ServerError:
        print(f"カード {card_index}: 503高負荷 → フォールバックモデル ({FALLBACK_MODEL}) でリトライ")
        return _call_model_with_retry(
            client, FALLBACK_MODEL, parts, card_index, total_cards, name, location,
        )


def _call_model_with_retry(
    client: genai.Client,
    model_name: str,
    parts: list[types.Part],
    card_index: int,
    total_cards: int,
    name: str,
    location: str,
    raise_on_503: bool = False,
) -> Optional[bytes]:
    """Call a Gemini model with one retry on error.

    Returns image bytes on success, or None on failure.
    If raise_on_503 is True, re-raises the ServerError on 503 so the
    caller can switch to a fallback model.
    """
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.models.generate_content(
                model=model_name,
                contents=[
                    types.Content(parts=parts)
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT", "IMAGE"],
                    safety_settings=_SAFETY_SETTINGS,
                ),
            )

            # Log finish reason for diagnostics
            finish_reason = None
            safety_ratings = None
            if response.candidates:
                candidate = response.candidates[0]
                finish_reason = getattr(candidate, "finish_reason", None)
                safety_ratings = getattr(candidate, "safety_ratings", None)
                if finish_reason:
                    print(f"カード {card_index}: finish_reason={finish_reason} ({model_name})")
                if safety_ratings:
                    print(f"カード {card_index}: safety_ratings={safety_ratings} ({model_name})")

            # Check for safety block
            if finish_reason and finish_reason in _SAFETY_FINISH_REASONS:
                print(f"カード {card_index}: SAFETY BLOCKED ({finish_reason}) ({model_name}), attempt {attempt}/{max_attempts}")
                if attempt < max_attempts:
                    time.sleep(2)
                    continue
                return None

            # Extract image from response
            candidate_content = response.candidates[0].content if response.candidates else None
            if candidate_content and candidate_content.parts:
                for part in candidate_content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        print(f"生成成功: カード {card_index} / {total_cards} ({model_name})")
                        return base64.b64decode(part.inline_data.data) if isinstance(part.inline_data.data, str) else part.inline_data.data

            # No image in response — log and retry
            if attempt < max_attempts:
                print(f"カード {card_index}: レスポンスに画像なし (finish_reason={finish_reason}) ({model_name}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"カード {card_index}: レスポンスに画像なし (finish_reason={finish_reason}) ({model_name}), 最終失敗")

        except ServerError as e:
            if e.code == 503 and raise_on_503:
                print(f"カード {card_index}: 503 高負荷エラー ({model_name})")
                raise
            if attempt < max_attempts:
                print(f"カード {card_index}: ServerError ({e}) ({model_name}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"Gemini API error for card {card_index} ({model_name}, final): {e}")
        except RuntimeError:
            raise
        except TypeError as e:
            if attempt < max_attempts:
                print(f"カード {card_index}: TypeError ({e}) ({model_name}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"Gemini API error for card {card_index} ({model_name}, final): {e}")
        except Exception as e:
            if attempt < max_attempts:
                print(f"カード {card_index}: エラー ({e}) ({model_name}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue
            print(f"Gemini API error for card {card_index} ({model_name}, final): {e}")

    return None
