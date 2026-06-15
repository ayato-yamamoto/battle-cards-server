
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

# Card themes for 5 battle cards (水テーマは一旦使用しない)
# 旧テーマ:
#   "(水)テーマは、青緑の水と浮遊する泡",
CARD_THEMES = [
    "(炎)テーマは、オレンジと赤の炎と燃える粒子",
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

    # Build prompt text based on whether template is provided
    if template_bytes:
        prompt = (
            f"1枚目の画像はバトルカードのテンプレート（背景フレーム）です。\n"
            f"2枚目の画像は人物の写真です。\n\n"
            f"このテンプレートカードの背景・フレームデザインを維持したまま、"
            f"人物の写真をバトルカード風にアレンジして配置してください。\n\n"
            f"テーマ: {theme}\n\n"
            f"【必須ルール】\n"
            f"- テンプレート画像のカード枠・装飾・背景デザイン・画像下部の名前入力用のスペースをそのまま使用すること\n"
            f"- このバトルカードのテーマに合う衣装に着せ替え、髪型もテーマに合わせて加工すること\n"
            f"- 顔は写真のままでイラスト風にしないこと。添付した画像の人はイラスト風などにはせず写真を元に加工すること\n"
            # f"- 人物の顔や体の大きさは統一すること。人物を配置する上下左右のバランスも統一すること\n"
            f"- 人物の画像を加工する時の顔や体の角度、縮尺は変更しないこと\n"
            # f"- カードの配置やサイズ、雰囲気は他のカードと統一すること\n"
            f"- 日本語や英語などの言語は出力しないこと\n"
            f"- インクジェットプリンターで印刷しても色が潰れないよう、明るさを最適に調整すること\n\n"
            f"【出力仕様】\n"
            f"- 印刷サイズ: 63mm × 88mm\n"
            f"- 解像度: 600 DPI\n"
            f"- 必要なピクセル数: 1488 × 2079 ピクセル\n"
            f"- ステータス表示、名前表示は含まない\n"
        )
    else:
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
            f"【出力仕様】\n"
            f"- 印刷サイズ: 63mm × 88mm\n"
            f"- 解像度: 600 DPI\n"
            f"- 必要なピクセル数: 1488 × 2079 ピクセル\n"
            f"- ステータス表示、名前表示は含まない\n"
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
                ),
            )

            # Extract image from response
            if response.candidates:
                print(f"生成成功: カード {card_index} / {total_cards} ({model_name})")
                for part in response.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.mime_type.startswith("image/"):
                        return base64.b64decode(part.inline_data.data) if isinstance(part.inline_data.data, str) else part.inline_data.data

            # No image in response — treat as retryable
            if attempt < max_attempts:
                print(f"カード {card_index}: レスポンスに画像なし ({model_name}), リトライ (attempt {attempt}/{max_attempts})")
                time.sleep(2)
                continue

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
