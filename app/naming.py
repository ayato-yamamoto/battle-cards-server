"""Naming logic for battle card characters.

Each card theme has 5 name templates. A template consists of a prefix and
an optional suffix. The user's first name (in katakana) is inserted between
them to form the final battle card name.

Example: prefix="炎帝ファイア", suffix=None, name="ハルト"
         → "炎帝ファイア ハルト"

Example: prefix="灼熱剣士", suffix="ナイト", name="ハルト"
         → "灼熱剣士 ハルト ナイト"
"""

import random
from dataclasses import dataclass


@dataclass
class NameTemplate:
    prefix: str
    suffix: str | None


# Mapping from card index (1-5) to theme name templates.
# 水テーマは一旦使用しない（5枚生成+広告モード固定のため）
# Card 1: 炎・火属性系 (Fire)
# Card 2: 雷・電気属性系 (Thunder)  ← 旧Card 3
# Card 3: 自然 (Nature)                   ← 旧Card 4
# Card 4: 虚無 (Void)                     ← 旧Card 5
# Card 5: 光・聖属性系 (Light)           ← 旧Card 6
# Card 6: 広告カード（生成対象外）
THEME_NAMES: dict[int, list[NameTemplate]] = {
    # 炎・火属性系
    1: [
        NameTemplate(prefix="炎帝ファイア", suffix=None),
        NameTemplate(prefix="紅蓮の戦士", suffix=None),
        NameTemplate(prefix="灼熱剣士", suffix="ナイト"),
        NameTemplate(prefix="火竜皇", suffix="ドラゴン"),
        NameTemplate(prefix="煉獄魔神", suffix="フェルノ"),
    ],
    # 氷・水属性系（一旦使用しない）
    # 2: [
    #     NameTemplate(prefix="氷結王アイス", suffix=None),
    #     NameTemplate(prefix="蒼氷の騎士", suffix="ナイト"),
    #     NameTemplate(prefix="氷雪姫", suffix="プリンセス"),
    #     NameTemplate(prefix="氷河竜", suffix="ドラゴン"),
    #     NameTemplate(prefix="凍魔導士", suffix="ウィザード"),
    # ],
    # 雷・電気属性系
    2: [
        NameTemplate(prefix="雷神皇サンダー", suffix=None),
        NameTemplate(prefix="電光剣士", suffix="ライト"),
        NameTemplate(prefix="雷鳴戦士", suffix="ウォリアー"),
        NameTemplate(prefix="嵐竜", suffix="ドラゴン"),
        NameTemplate(prefix="雷撃魔人", suffix="デーモン"),
    ],
    # 自然 (風・大気 + 地・土)
    3: [
        NameTemplate(prefix="疾風王ウィンド", suffix=None),
        NameTemplate(prefix="大地王", suffix="キング"),
        NameTemplate(prefix="天空竜", suffix="ドラゴン"),
        NameTemplate(prefix="岩石巨人ロック", suffix=None),
        NameTemplate(prefix="暴風の", suffix="ウォリアー"),
    ],
    # 虚無 (闇・邪属性系)
    4: [
        NameTemplate(prefix="闇帝", suffix="エンペラー"),
        NameTemplate(prefix="黒騎士ブラック", suffix=None),
        NameTemplate(prefix="堕天使", suffix="エンジェル"),
        NameTemplate(prefix="影竜", suffix="ドラゴン"),
        NameTemplate(prefix="邪悪魔王デビル", suffix=None),
    ],
    # 光・聖属性系
    5: [
        NameTemplate(prefix="光明皇", suffix="エンペラー"),
        NameTemplate(prefix="聖騎士ホーリー", suffix=None),
        NameTemplate(prefix="天使長", suffix="エンジェル"),
        NameTemplate(prefix="光竜", suffix="ドラゴン"),
        NameTemplate(prefix="魔導士", suffix="ウィザード"),
    ],
}


def generate_card_name(first_name: str, card_index: int, seed: str | None = None) -> str:
    """Generate a battle card name for the given first name and card index.

    Args:
        first_name: The user's first name (ideally in katakana).
        card_index: Card number 1-6.
        seed: Optional seed for deterministic selection. When provided,
              the same seed always produces the same template choice,
              making retries safe.

    Returns:
        The generated battle card name string.
    """
    templates = THEME_NAMES.get(card_index, THEME_NAMES[1])
    rng = random.Random(seed) if seed else random
    template = rng.choice(templates)

    if template.suffix:
        return f"{template.prefix} {first_name} {template.suffix}"
    return f"{template.prefix} {first_name}"
