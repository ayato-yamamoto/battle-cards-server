import base64
import os
from typing import Optional

from google import genai
from google.genai import types


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-2.5-flash-preview-04-17"


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
) -> Optional[bytes]:
    """Generate a single battle card image from a source photo using Gemini API.

    Returns the generated image bytes (PNG) or None if generation failed.
    """
    client = get_client()

    prompt = (
        f"Transform this photo into an epic battle trading card illustration. "
        f"The card should have a dramatic, anime-style art with vibrant colors and dynamic effects. "
        f"Card details: Player name is '{name}', location is '{location}'. "
        f"This is card {card_index} of {total_cards} in the set. "
        f"Make each card feel unique with different poses, effects, and color schemes. "
        f"Card {card_index} style: "
    )

    # Add variety to each card
    styles = [
        "Fire theme with red and orange flames, aggressive battle pose",
        "Ice theme with blue crystals and frost effects, defensive stance",
        "Lightning theme with electric yellow sparks, speed attack pose",
        "Nature theme with green vines and earth power, guardian stance",
        "Shadow theme with purple dark energy, mysterious floating pose",
        "Light theme with golden holy aura, victorious celebration pose",
    ]
    style_index = (card_index - 1) % len(styles)
    prompt += styles[style_index]

    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=[
                types.Content(
                    parts=[
                        types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        types.Part.from_text(text=prompt),
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                response_modalities=["TEXT", "IMAGE"],
            ),
        )

        # Extract image from response
        if response.candidates:
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    return base64.b64decode(part.inline_data.data) if isinstance(part.inline_data.data, str) else part.inline_data.data

    except Exception as e:
        print(f"Gemini API error for card {card_index}: {e}")

    return None
