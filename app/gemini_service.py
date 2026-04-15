import base64
import os
from typing import Optional

from google import genai
from google.genai import types


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
MODEL_NAME = "gemini-3-pro-image-preview"

# Card themes for 6 battle cards
CARD_THEMES = [
    "Fire theme — flames and volcanic background, warrior outfit with red armor",
    "Ice theme — frost crystals and snowy background, knight outfit with blue armor",
    "Lightning theme — electric sparks and stormy background, speed fighter outfit with yellow accents",
    "Nature theme — vines and forest background, ranger outfit with green cloak",
    "Shadow theme — dark energy and night background, assassin outfit with purple cape",
    "Light theme — holy aura and heavenly background, paladin outfit with golden armor",
]


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

    theme = CARD_THEMES[(card_index - 1) % len(CARD_THEMES)]

    prompt = (
        f"添付の人物の画像をバトルカード風にアレンジしてください。\n"
        f"カード {card_index} / {total_cards} 枚目のテーマ: {theme}\n\n"
        f"【必須ルール】\n"
        f"- このバトルカードのテーマに合う衣装に着せ替え、髪型もテーマに合わせて加工すること\n"
        f"- 顔は写真のままでイラスト風にしないこと。添付した画像の人はイラスト風などにはせず写真を元に加工すること\n"
        f"- 人物の顔や体の大きさは統一すること。人物を配置する上下左右のバランスも統一すること\n"
        f"- カードの配置やサイズ、雰囲気は他のカードと統一すること\n"
        f"- インクジェットプリンターで印刷しても色が潰れないよう、明るさを最適に調整すること\n\n"
        f"【カード情報】\n"
        f"- プレイヤー名: {name}\n"
        f"- ロケーション: {location}\n\n"
        f"【出力仕様】\n"
        f"- 印刷サイズ: 63mm × 88mm\n"
        f"- 解像度: 600 DPI\n"
        f"- 必要なピクセル数: 1488 × 2079 ピクセル\n"
        f"- バトルカードのフォーマットで出力すること（枠、ステータス表示、名前表示を含む）\n"
    )

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

    except RuntimeError:
        raise
    except Exception as e:
        print(f"Gemini API error for card {card_index}: {e}")

    return None
