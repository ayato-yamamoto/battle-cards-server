import base64
import os
from typing import Optional

from google import genai
from google.genai import types


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# MODEL_NAME = "gemini-3.1-flash-image-preview"
MODEL_NAME = "gemini-2.5-flash"

# Card themes for 6 battle cards
CARD_THEMES = [
    "(炎)テーマは、オレンジと赤の炎と燃える粒子",
    "(水)テーマは、青緑の水と浮遊する泡",
    "(雷)テーマは、青紫の稲妻と電気エネルギー",
    "(自然)テーマは、緑の魔法オーラと浮遊する葉",
    "(虚無)テーマは、紫と黒の神秘的なエネルギー",
    "(光)テーマは、金色の神聖な光と輝き",
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
    print(f"生成開始: カード {card_index} / {total_cards} - {name} at {location}")
    client = get_client()

    theme = CARD_THEMES[(card_index - 1) % len(CARD_THEMES)]

    prompt = (
        f"添付の人物の画像をバトルカード風にアレンジしてください。\n"
        f"テーマ: {theme}\n\n"
        f"【必須ルール】\n"
        f"- このバトルカードのテーマに合う衣装に着せ替え、髪型もテーマに合わせて加工すること\n"
        f"- 顔は写真のままでイラスト風にしないこと。添付した画像の人はイラスト風などにはせず写真を元に加工すること\n"
        f"- 人物の顔や体の大きさは統一すること。人物を配置する上下左右のバランスも統一すること\n"
        f"- カードの配置やサイズ、雰囲気は他のカードと統一すること\n"
        f"- 日本語や英語などの言語は出力しないこと\n"
        f"- インクジェットプリンターで印刷しても色が潰れないよう、明るさを最適に調整すること\n\n"
        # f"【カード情報】\n"
        # f"- プレイヤー名: {name}\n"
        # f"- ロケーション: {location}\n\n"
        f"【出力仕様】\n"
        f"- 印刷サイズ: 63mm × 88mm\n"
        f"- 解像度: 600 DPI\n"
        f"- 必要なピクセル数: 1488 × 2079 ピクセル\n"
        f"- ステータス表示、名前表示は含まない\n"
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
            print(f"生成成功: カード {card_index} / {total_cards} - {name} at {location}")
            for part in response.candidates[0].content.parts:
                if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                    return base64.b64decode(part.inline_data.data) if isinstance(part.inline_data.data, str) else part.inline_data.data

    except RuntimeError:
        raise
    except Exception as e:
        print(f"Gemini API error for card {card_index}: {e}")

    return None
