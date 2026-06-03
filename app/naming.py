"""Naming logic for battle card characters.

Each card theme has 5 name templates. A template consists of a prefix and
an optional suffix. The user's first name (in katakana) is inserted between
them to form the final battle card name.

Kanji portions of the prefix carry a reading (hiragana) that is rendered
as small ruby text above the kanji on the card.

Example: prefix="炎帝ファイア", suffix=None, name="ハルト"
         → display "炎帝ファイア ハルト", ruby "えんてい" above "炎帝"
"""

import random
from dataclasses import dataclass


@dataclass
class NameTemplate:
    prefix: str
    suffix: str | None
    prefix_kanji: str
    prefix_kanji_reading: str


@dataclass
class CardNameResult:
    """Result of generating a battle card name."""

    display: str
    ruby_target: str
    ruby_reading: str


# Mapping from card index (1-5) to theme name templates.
# Card 1: 炎 (Fire)
# Card 2: 雷 (Thunder)
# Card 3: 自然 (Nature)
# Card 4: 闇 (Void/Dark)
# Card 5: 光 (Light)
# Card 6: 広告カード（生成対象外）
THEME_NAMES: dict[int, list[NameTemplate]] = {
    # 🔥炎
    1: [
        NameTemplate("炎帝ファイア", None, "炎帝", "えんてい"),
        NameTemplate("紅蓮騎士", "ブレイズ", "紅蓮騎士", "ぐれんきし"),
        NameTemplate("灼熱剣士", "イグナイト", "灼熱剣士", "しゃくねつけんし"),
        NameTemplate("火竜王", "フレイムドラゴン", "火竜王", "かりゅうおう"),
        NameTemplate("煉獄魔神", "フェルノ", "煉獄魔神", "れんごくまじん"),
    ],
    # ⚡雷
    2: [
        NameTemplate("雷神皇サンダー", None, "雷神皇", "らいしんこう"),
        NameTemplate("迅雷騎士", "ボルトナイト", "迅雷騎士", "じんらいきし"),
        NameTemplate("電光剣士", "ライトニング", "電光剣士", "でんこうけんし"),
        NameTemplate("雷竜王", "ボルテックスドラゴン", "雷竜王", "らいりゅうおう"),
        NameTemplate("雷霆魔人", "エレクトロ", "雷霆魔人", "らいていまじん"),
    ],
    # 🌳自然
    3: [
        NameTemplate("森羅王ネイチャー", None, "森羅王", "しんらおう"),
        NameTemplate("樹海騎士", "リーフナイト", "樹海騎士", "じゅかいきし"),
        NameTemplate("精霊使い", "スピリット", "精霊使い", "せいれいつかい"),
        NameTemplate("世界樹竜", "ユグドラシル", "世界樹竜", "せかいじゅりゅう"),
        NameTemplate("森霊王", "ドライアド", "森霊王", "しんれいおう"),
    ],
    # 🌑闇
    4: [
        NameTemplate("闇帝ダーク", None, "闇帝", "あんてい"),
        NameTemplate("黒騎士", "ブラックナイト", "黒騎士", "くろきし"),
        NameTemplate("堕天使", "フォールン", "堕天使", "だてんし"),
        NameTemplate("影竜", "シャドウドラゴン", "影竜", "えいりゅう"),
        NameTemplate("邪悪魔王", "アビスロード", "邪悪魔王", "じゃあくまおう"),
    ],
    # ✨光
    5: [
        NameTemplate("光明皇ライト", None, "光明皇", "こうめいこう"),
        NameTemplate("聖騎士", "ホーリー", "聖騎士", "せいきし"),
        NameTemplate("天使長", "セラフィム", "天使長", "てんしちょう"),
        NameTemplate("光竜", "シャイニングドラゴン", "光竜", "こうりゅう"),
        NameTemplate("神聖魔導士", "アークメイジ", "神聖魔導士", "しんせいまどうし"),
    ],
}


def generate_card_name(
    first_name: str, card_index: int, seed: str | None = None
) -> CardNameResult:
    """Generate a battle card name for the given first name and card index.

    Args:
        first_name: The user's first name (ideally in katakana).
        card_index: Card number 1-6.
        seed: Optional seed for deterministic selection.

    Returns:
        A CardNameResult with display name, ruby target, and ruby reading.
    """
    templates = THEME_NAMES.get(card_index, THEME_NAMES[1])
    rng = random.Random(seed) if seed else random
    template = rng.choice(templates)

    if template.suffix:
        display = f"{template.prefix} {first_name} {template.suffix}"
    else:
        display = f"{template.prefix} {first_name}"

    return CardNameResult(
        display=display,
        ruby_target=template.prefix_kanji,
        ruby_reading=template.prefix_kanji_reading,
    )
